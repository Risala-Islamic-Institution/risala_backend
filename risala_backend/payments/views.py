import json
import logging
import uuid
from decimal import Decimal
import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views import View
from rest_framework import status, permissions, parsers
from rest_framework.response import Response
from rest_framework.views import APIView

from risala_backend.users.models import SessionBooking, BookingOrder, Notification
from risala_backend.payments.models import Payment, PaymentGatewayConfig
from risala_backend.payments.services import ChapaService, ShegerService
from risala_backend.courses.models import Course, Enrollment

logger = logging.getLogger(__name__)
stripe.api_key = settings.STRIPE_SECRET_KEY
User = get_user_model()


def _confirm_order_payment(order: BookingOrder, payment: Payment, note: str = ""):
    """
    Atomically confirms a BookingOrder, its linked TimeSlots/SessionBookings,
    and dispatches student/teacher notifications.
    """
    with transaction.atomic():
        order.status = BookingOrder.Status.PAID
        order.save(update_fields=["status", "updated_at"])

        # Mark bookings confirmed
        order.bookings.all().update(status=SessionBooking.Status.CONFIRMED)

        # Update payment record
        payment.status = Payment.Status.COMPLETED
        if note:
            payment.admin_note = note
        payment.save()

        # Send notifications
        teacher_user = getattr(order.teacher, "user", None)
        student_user = getattr(order.student, "user", None)
        if teacher_user and student_user:
            num_sessions = order.bookings.count()
            Notification.objects.create(
                user=teacher_user,
                title="Lesson Package Paid & Confirmed",
                body=f"Student {student_user.full_name or student_user.username} paid for {num_sessions} session(s).",
            )
            Notification.objects.create(
                user=student_user,
                title="Booking Confirmed",
                body=f"Your payment of {payment.amount} {payment.currency.upper()} was confirmed. All {num_sessions} sessions are scheduled!",
            )


def _confirm_course_enrollment(course: Course, user, payment: Payment, note: str = ""):
    """
    Atomically enrolls a student in a paid course and notifies student and instructor.
    """
    with transaction.atomic():
        if hasattr(user, "student_profile"):
            student = user.student_profile
            enrollment, created = Enrollment.objects.get_or_create(
                course=course,
                student=student,
                defaults={"status": Enrollment.Status.ENROLLED}
            )

        payment.status = Payment.Status.COMPLETED
        if note:
            payment.admin_note = note
        payment.save()

        Notification.objects.create(
            user=user,
            title="Course Enrollment Successful",
            body=f"You are now enrolled in {course.title}.",
        )
        if getattr(course.created_by, "user", None):
            Notification.objects.create(
                user=course.created_by.user,
                title="New Student Enrolled",
                body=f"{user.full_name or user.username} enrolled in {course.title}.",
            )


class PaymentMethodsView(APIView):
    """
    Returns active payment gateways configured by the administrator.
    Client app uses this to dynamically display available checkout options.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request, *args, **kwargs):
        config = PaymentGatewayConfig.get_solo()
        methods = []

        if config.stripe_enabled:
            methods.append({
                "id": "STRIPE",
                "title": "Credit / Debit Card",
                "description": "International Visa, Mastercard, AMEX via Stripe",
                "icon": "credit_card",
                "currencies": ["usd", "etb"],
            })

        if config.chapa_enabled:
            methods.append({
                "id": "CHAPA",
                "title": "Chapa (Telebirr, CBE Birr, Awash)",
                "description": "Instant payment via Telebirr, CBE Birr, or Ethiopian cards",
                "icon": "payments",
                "currencies": ["etb"],
            })

        if config.manual_bank_enabled:
            methods.append({
                "id": "MANUAL_BANK",
                "title": "Bank Transfer / Telebirr",
                "description": "Direct transfer to official bank accounts with transaction reference",
                "icon": "account_balance",
                "currencies": ["etb"],
                "verification_mode": config.manual_verification_mode,
                "bank_accounts": config.bank_accounts or [],
                "instructions": config.manual_payment_instructions or "",
            })

        return Response({
            "payment_methods": methods,
            "stripe_enabled": config.stripe_enabled,
            "chapa_enabled": config.chapa_enabled,
            "manual_bank_enabled": config.manual_bank_enabled,
            "manual_verification_mode": config.manual_verification_mode,
        }, status=status.HTTP_200_OK)


class CreateCheckoutSessionView(APIView):
    """
    Initiates payment for either a BookingOrder or Course.
    Supports STRIPE and CHAPA gateways based on admin toggle configuration.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        config = PaymentGatewayConfig.get_solo()
        payment_method = request.data.get("payment_method", "STRIPE").upper()

        order_id = request.data.get("order_id")
        booking_id = request.data.get("booking_id")
        course_id = request.data.get("course_id")

        if not (order_id or booking_id or course_id):
            return Response(
                {"error": "order_id, booking_id, or course_id is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate gateway activation
        if payment_method == "CHAPA":
            if not config.chapa_enabled:
                return Response(
                    {"error": "Chapa payment gateway is currently disabled by administrator."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            return self._handle_chapa_checkout(request, order_id, booking_id, course_id)

        elif payment_method == "STRIPE":
            if not config.stripe_enabled:
                return Response(
                    {"error": "Stripe payment gateway is currently disabled by administrator."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            return self._handle_stripe_checkout(request, order_id, booking_id, course_id)

        else:
            return Response(
                {"error": f"Unsupported online payment method: {payment_method}"},
                status=status.HTTP_400_BAD_REQUEST
            )

    def _handle_chapa_checkout(self, request, order_id, booking_id, course_id):
        """Initializes a Chapa payment transaction."""
        success_url = request.data.get("return_url") or "risala://payment/chapa-success"
        callback_url = request.build_absolute_uri("/api/v1/payments/chapa/webhook/")

        # Course purchase
        if course_id:
            try:
                course = Course.objects.get(id=course_id)
            except Course.DoesNotExist:
                return Response({"error": "Course not found"}, status=status.HTTP_404_NOT_FOUND)

            if course.price <= 0:
                _confirm_course_enrollment(course, request.user, Payment(amount=0, currency="etb", status=Payment.Status.COMPLETED))
                return Response({'sessionId': 'free_bypass', 'checkout_url': 'risala://payment/success?session_id=free_bypass'})

            amount = course.price
            tx_ref = f"course-{course.id}-{uuid.uuid4().hex[:8]}"
            title = f"Course: {course.title[:40]}"
            description = "Course Enrollment"

            chapa_res = ChapaService.initialize_transaction(
                amount=amount,
                currency="ETB",
                email=request.user.email,
                first_name=request.user.first_name or request.user.username,
                last_name=request.user.last_name or ".",
                tx_ref=tx_ref,
                callback_url=callback_url,
                return_url=f"{success_url}?tx_ref={tx_ref}",
                title=title,
                description=description,
            )

            if not chapa_res.get("success"):
                return Response({"error": chapa_res.get("error", "Chapa checkout failed")}, status=status.HTTP_400_BAD_REQUEST)

            Payment.objects.create(
                user=request.user,
                course=course,
                payment_method=Payment.PaymentMethod.CHAPA,
                tx_ref=tx_ref,
                amount=amount,
                currency="etb",
                status=Payment.Status.PENDING,
            )
            return Response({
                "sessionId": tx_ref,
                "tx_ref": tx_ref,
                "checkout_url": chapa_res["checkout_url"],
                "payment_method": "CHAPA",
            })

        # Booking Order
        if booking_id and not order_id:
            order_id = self._ensure_order_from_booking(request, booking_id)

        try:
            order = BookingOrder.objects.get(id=order_id)
        except BookingOrder.DoesNotExist:
            return Response({"error": "Order not found"}, status=status.HTTP_404_NOT_FOUND)

        if order.student.user != request.user:
            return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)

        if order.status not in [BookingOrder.Status.PENDING, BookingOrder.Status.APPROVED]:
            return Response({"error": f"Order is in {order.status} state, cannot be paid."}, status=status.HTTP_400_BAD_REQUEST)

        amount = order.total_amount
        tx_ref = f"order-{order.id}-{uuid.uuid4().hex[:8]}"
        title = f"Lesson Package ({order.bookings.count()} sessions)"
        description = f"Teacher: {order.teacher.user.full_name or order.teacher.user.username}"

        chapa_res = ChapaService.initialize_transaction(
            amount=amount,
            currency="ETB",
            email=request.user.email,
            first_name=request.user.first_name or request.user.username,
            last_name=request.user.last_name or ".",
            tx_ref=tx_ref,
            callback_url=callback_url,
            return_url=f"{success_url}?tx_ref={tx_ref}",
            title=title,
            description=description,
        )

        if not chapa_res.get("success"):
            return Response({"error": chapa_res.get("error", "Chapa checkout failed")}, status=status.HTTP_400_BAD_REQUEST)

        Payment.objects.create(
            user=request.user,
            order=order,
            payment_method=Payment.PaymentMethod.CHAPA,
            tx_ref=tx_ref,
            amount=amount,
            currency="etb",
            status=Payment.Status.PENDING,
        )
        return Response({
            "sessionId": tx_ref,
            "tx_ref": tx_ref,
            "checkout_url": chapa_res["checkout_url"],
            "payment_method": "CHAPA",
        })

    def _handle_stripe_checkout(self, request, order_id, booking_id, course_id):
        """Standard Stripe Checkout Flow."""
        if course_id:
            return self._handle_stripe_course(request, course_id)
        if booking_id and not order_id:
            order_id = self._ensure_order_from_booking(request, booking_id)
        return self._handle_stripe_order(request, order_id)

    def _handle_stripe_course(self, request, course_id):
        try:
            course = Course.objects.get(id=course_id)
        except Course.DoesNotExist:
            return Response({"error": "Course not found"}, status=status.HTTP_404_NOT_FOUND)

        if course.price <= 0:
            _confirm_course_enrollment(course, request.user, Payment(amount=0, currency="usd", status=Payment.Status.COMPLETED))
            return Response({'sessionId': 'free_bypass', 'checkout_url': 'risala://payment/success?session_id=free_bypass'})

        amount_cents = int(course.price * 100)
        currency = getattr(settings, 'PAYMENT_DEFAULT_CURRENCY', 'usd')
        success_url = request.data.get("success_url", settings.STRIPE_SUCCESS_URL)
        cancel_url = request.data.get("cancel_url", settings.STRIPE_CANCEL_URL)

        try:
            checkout_session = stripe.checkout.Session.create(
                line_items=[{
                    'price_data': {
                        'currency': currency,
                        'product_data': {
                            'name': course.title,
                            'description': "Course Enrollment",
                        },
                        'unit_amount': amount_cents,
                    },
                    'quantity': 1,
                }],
                metadata={'course_id': str(course.id)},
                mode='payment',
                success_url=success_url,
                cancel_url=cancel_url,
                client_reference_id=str(request.user.id),
            )
            return Response({'sessionId': checkout_session.id, 'checkout_url': checkout_session.url, 'payment_method': 'STRIPE'})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _handle_stripe_order(self, request, order_id):
        try:
            order = BookingOrder.objects.get(id=order_id)
        except BookingOrder.DoesNotExist:
            return Response({"error": "Order not found"}, status=status.HTTP_404_NOT_FOUND)

        if order.student.user != request.user:
            return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)

        if order.status not in [BookingOrder.Status.PENDING, BookingOrder.Status.APPROVED]:
            return Response({"error": f"Order is in {order.status} state, cannot be paid."}, status=status.HTTP_400_BAD_REQUEST)

        amount = order.total_amount
        amount_cents = int(amount * 100)
        currency = getattr(settings, 'PAYMENT_DEFAULT_CURRENCY', 'usd')
        num_sessions = order.bookings.count()
        success_url = request.data.get("success_url", settings.STRIPE_SUCCESS_URL)
        cancel_url = request.data.get("cancel_url", settings.STRIPE_CANCEL_URL)

        try:
            checkout_session = stripe.checkout.Session.create(
                line_items=[{
                    'price_data': {
                        'currency': currency,
                        'product_data': {
                            'name': f"Lesson Package with {order.teacher.user.full_name or order.teacher.user.username}",
                            'description': f"{num_sessions} session(s)",
                        },
                        'unit_amount': amount_cents,
                    },
                    'quantity': 1,
                }],
                metadata={'order_id': str(order.id)},
                mode='payment',
                success_url=success_url,
                cancel_url=cancel_url,
                client_reference_id=str(request.user.id),
            )

            Payment.objects.update_or_create(
                order=order,
                defaults={
                    'user': request.user,
                    'payment_method': Payment.PaymentMethod.STRIPE,
                    'stripe_checkout_id': checkout_session.id,
                    'amount': amount,
                    'currency': currency,
                    'status': Payment.Status.PENDING
                }
            )
            return Response({'sessionId': checkout_session.id, 'checkout_url': checkout_session.url, 'payment_method': 'STRIPE'})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _ensure_order_from_booking(self, request, booking_id):
        booking = SessionBooking.objects.get(id=booking_id)
        if booking.order:
            return str(booking.order.id)
        duration = booking.end_at - booking.start_at
        hours = Decimal(str(duration.total_seconds() / 3600))
        rate = booking.hourly_rate or booking.teacher.hourly_rate or Decimal("10.00")
        total = max(hours * rate, Decimal("1.00"))
        with transaction.atomic():
            order = BookingOrder.objects.create(
                student=booking.student,
                teacher=booking.teacher,
                total_amount=total,
                currency="etb",
                status=BookingOrder.Status.PENDING,
            )
            booking.order = order
            booking.status = SessionBooking.Status.RESERVED
            booking.save(update_fields=["order", "status", "updated_at"])
        return str(order.id)


class SubmitManualPaymentView(APIView):
    """
    Accepts student manual bank transfer / Telebirr transaction reference ID and optional receipt.
    Depending on admin configuration, either:
    1. Instantly validates via Sheger API (if mode is SHEGER_API and verification succeeds) -> COMPLETED
    2. Places in UNDER_REVIEW for staff manual one-click approval -> UNDER_REVIEW
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]

    def post(self, request, *args, **kwargs):
        config = PaymentGatewayConfig.get_solo()
        if not config.manual_bank_enabled:
            return Response(
                {"error": "Manual bank transfer is currently disabled by administrator."},
                status=status.HTTP_400_BAD_REQUEST
            )

        transaction_id = (request.data.get("transaction_id") or "").strip()
        receipt_image = request.FILES.get("receipt_image")
        order_id = request.data.get("order_id")
        course_id = request.data.get("course_id")
        bank_name = (request.data.get("bank_name") or "").strip()

        # At least one proof of transfer must be provided (either reference ID or receipt screenshot)
        if not transaction_id and not receipt_image:
            return Response(
                {"error": "Please enter a transaction reference number or upload your payment receipt screenshot."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Generate provisional reference if only receipt image was attached
        if not transaction_id:
            transaction_id = f"SLIP-{uuid.uuid4().hex[:8].upper()}"

        order = None
        course = None
        amount = Decimal("0.00")

        if order_id:
            try:
                order = BookingOrder.objects.get(id=order_id)
            except BookingOrder.DoesNotExist:
                return Response({"error": "Order not found."}, status=status.HTTP_404_NOT_FOUND)
            if order.student.user != request.user:
                return Response({"error": "Unauthorized."}, status=status.HTTP_403_FORBIDDEN)
            amount = order.total_amount
        elif course_id:
            try:
                course = Course.objects.get(id=course_id)
            except Course.DoesNotExist:
                return Response({"error": "Course not found."}, status=status.HTTP_404_NOT_FOUND)
            amount = course.price
        else:
            return Response({"error": "Either order_id or course_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Look for existing pending payment record for this order/course, or create new
        tx_ref = f"manual-{transaction_id}-{uuid.uuid4().hex[:6]}"
        payment = None
        if order:
            payment = Payment.objects.filter(order=order, status=Payment.Status.UNDER_REVIEW).first()
        elif course:
            payment = Payment.objects.filter(course=course, user=request.user, status=Payment.Status.UNDER_REVIEW).first()

        if payment:
            payment.manual_transaction_id = transaction_id
            payment.bank_name = bank_name or payment.bank_name
            payment.amount = amount
            if receipt_image:
                payment.manual_receipt_image = receipt_image
            payment.save()
        else:
            payment = Payment.objects.create(
                user=request.user,
                order=order,
                course=course,
                payment_method=Payment.PaymentMethod.MANUAL_BANK,
                tx_ref=tx_ref,
                manual_transaction_id=transaction_id,
                manual_receipt_image=receipt_image,
                amount=amount,
                currency="etb",
                bank_name=bank_name,
                status=Payment.Status.UNDER_REVIEW,
                verified_by=Payment.VerifiedBy.MANUAL_ADMIN,
            )

        # Check if automated Sheger API verification is configured
        if config.manual_verification_mode == PaymentGatewayConfig.VerificationMode.SHEGER_API:
            sheger_result = ShegerService.verify_payment_record(payment, actor=request.user)
            if sheger_result.get("verified") is True:
                return Response({
                    "status": "COMPLETED",
                    "verified": True,
                    "payment_id": str(payment.id),
                    "message": "Payment verified automatically via ShegerPay! Your order is confirmed.",
                }, status=status.HTTP_200_OK)

        # Notify student of submission
        Notification.objects.create(
            user=request.user,
            title="Payment Submitted for Review",
            body=f"Your payment proof '{transaction_id}' was received. Our team will verify it shortly.",
        )

        return Response({
            "status": "UNDER_REVIEW",
            "verified": False,
            "payment_id": str(payment.id),
            "message": "Your transfer reference was submitted successfully and is pending administrator review.",
        }, status=status.HTTP_200_OK)



class ChapaVerifyView(APIView):
    """
    Authoritative client polling/verification endpoint for Chapa transactions.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        tx_ref = request.data.get("tx_ref")
        if not tx_ref:
            return Response({"error": "tx_ref is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            payment = Payment.objects.get(tx_ref=tx_ref)
        except Payment.DoesNotExist:
            return Response({"error": "Transaction not found."}, status=status.HTTP_404_NOT_FOUND)

        if payment.status == Payment.Status.COMPLETED:
            return Response({
                "status": "paid",
                "payment_status": "paid",
                "message": "Payment already confirmed."
            }, status=status.HTTP_200_OK)

        verify_res = ChapaService.verify_transaction(tx_ref)
        if verify_res.get("is_paid"):
            payment.chapa_reference = verify_res.get("reference", "")
            payment.verified_by = Payment.VerifiedBy.WEBHOOK
            if payment.order:
                _confirm_order_payment(payment.order, payment, note="Verified via Chapa API")
            elif payment.course and payment.user:
                _confirm_course_enrollment(payment.course, payment.user, payment, note="Verified via Chapa API")

            return Response({
                "status": "paid",
                "payment_status": "paid",
                "message": "Chapa payment verified successfully."
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                "status": "pending",
                "payment_status": "pending",
                "message": verify_res.get("error", "Payment is still processing on Chapa.")
            }, status=status.HTTP_200_OK)


@method_decorator(csrf_exempt, name='dispatch')
class ChapaWebhookView(View):
    """
    Asynchronous Webhook receiver from Chapa servers.
    """
    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            return HttpResponse(status=400)

        tx_ref = data.get("tx_ref")
        if not tx_ref:
            return HttpResponse(status=400)

        try:
            payment = Payment.objects.get(tx_ref=tx_ref)
        except Payment.DoesNotExist:
            return HttpResponse(status=404)

        if payment.status == Payment.Status.COMPLETED:
            return HttpResponse(status=200)

        verify_res = ChapaService.verify_transaction(tx_ref)
        if verify_res.get("is_paid"):
            payment.chapa_reference = verify_res.get("reference", "")
            payment.verified_by = Payment.VerifiedBy.WEBHOOK
            if payment.order:
                _confirm_order_payment(payment.order, payment, note="Confirmed by Chapa Webhook")
            elif payment.course and payment.user:
                _confirm_course_enrollment(payment.course, payment.user, payment, note="Confirmed by Chapa Webhook")

        return HttpResponse(status=200)


@method_decorator(csrf_exempt, name='dispatch')
class StripeWebhookView(View):
    def post(self, request, *args, **kwargs):
        payload = request.body
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
            )
        except Exception as e:
            logger.error(f"Stripe webhook verification failed: {e}")
            return HttpResponse(status=400)

        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            self.handle_checkout_session_completed(session)

        return HttpResponse(status=200)

    def handle_checkout_session_completed(self, session):
        metadata = session.get('metadata', {})
        order_id = metadata.get('order_id')
        course_id = metadata.get('course_id')

        if course_id:
            try:
                course = Course.objects.get(id=course_id)
                user_id = session.get('client_reference_id')
                user = User.objects.get(id=user_id)
                payment, _ = Payment.objects.get_or_create(
                    stripe_checkout_id=session.get('id'),
                    defaults={'user': user, 'course': course, 'amount': course.price, 'currency': 'usd'}
                )
                _confirm_course_enrollment(course, user, payment, note="Confirmed via Stripe Webhook")
            except Exception as e:
                logger.error(f"Error processing course webhook: {e}")

        elif order_id:
            try:
                order = BookingOrder.objects.get(id=order_id)
                payment, _ = Payment.objects.get_or_create(
                    order=order,
                    defaults={'user': order.student.user, 'amount': order.total_amount, 'currency': 'usd'}
                )
                payment.stripe_payment_intent_id = session.get('payment_intent')
                _confirm_order_payment(order, payment, note="Confirmed via Stripe Webhook")
            except Exception as e:
                logger.error(f"Error processing order webhook: {e}")


class VerifyPaymentView(APIView):
    """
    Authoritative Stripe verification endpoint.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        session_id = request.data.get("session_id")
        if not session_id:
            return Response({"error": "session_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = stripe.checkout.Session.retrieve(session_id)
            if session.client_reference_id and str(session.client_reference_id) != str(request.user.id):
                return Response({"error": "Unauthorized session."}, status=status.HTTP_403_FORBIDDEN)

            if session.payment_status == "paid":
                handler = StripeWebhookView()
                handler.handle_checkout_session_completed(session)
                return Response({
                    "status": "paid",
                    "payment_status": "paid",
                    "message": "Payment verified and confirmed."
                })
            else:
                return Response({
                    "status": session.payment_status,
                    "payment_status": session.payment_status,
                    "message": "Payment has not been completed on Stripe."
                }, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CancelOrRefundOrderView(APIView):
    """
    Allows student/teacher to cancel order and releases reserved slots.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        order_id = request.data.get("order_id")
        if not order_id:
            return Response({"error": "order_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            order = BookingOrder.objects.get(id=order_id)
        except BookingOrder.DoesNotExist:
            return Response({"error": "Order not found"}, status=status.HTTP_404_NOT_FOUND)

        is_student = (order.student.user == request.user)
        is_teacher = (order.teacher.user == request.user)
        if not (is_student or is_teacher or request.user.is_staff):
            return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)

        from risala_backend.users.models import TimeSlot
        with transaction.atomic():
            refund_issued = False
            payment = getattr(order, "payment", None) or order.payments.filter(status=Payment.Status.COMPLETED).first()
            if payment and payment.payment_method == Payment.PaymentMethod.STRIPE and payment.stripe_payment_intent_id:
                try:
                    stripe.Refund.create(
                        payment_intent=payment.stripe_payment_intent_id,
                        reason="requested_by_customer" if is_student else "fraudulent",
                    )
                    payment.status = Payment.Status.REFUNDED
                    payment.save(update_fields=["status", "updated_at"])
                    refund_issued = True
                except Exception as e:
                    logger.error(f"Stripe refund failed: {e}")

            order.status = BookingOrder.Status.CANCELLED
            order.save(update_fields=["status", "updated_at"])
            TimeSlot.objects.filter(booking__in=order.bookings.all()).update(is_booked=False, booking=None)
            order.bookings.all().update(status=SessionBooking.Status.CANCELLED)

        return Response({
            "message": "Order cancelled successfully.",
            "refund_issued": refund_issued,
            "status": "CANCELLED",
        }, status=status.HTTP_200_OK)


class IsAdminUserPermission(permissions.BasePermission):
    """
    Ensures user is staff, superuser, or has the ADMIN role.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return bool(
            getattr(request.user, "is_staff", False)
            or getattr(request.user, "is_superuser", False)
            or (hasattr(request.user, "has_role") and request.user.has_role("ADMIN"))
        )


class AdminPaymentConfigView(APIView):
    """
    GET: Retrieve full gateway toggles and credentials (admin only).
    PATCH: Update toggles, verification mode, accounts, keys in real time.
    """
    permission_classes = [IsAdminUserPermission]

    def get(self, request, *args, **kwargs):
        cfg = PaymentGatewayConfig.get_solo()
        return Response({
            "stripe_enabled": cfg.stripe_enabled,
            "chapa_enabled": cfg.chapa_enabled,
            "manual_bank_enabled": cfg.manual_bank_enabled,
            "manual_verification_mode": cfg.manual_verification_mode,
            "chapa_public_key": cfg.chapa_public_key,
            "chapa_secret_key": cfg.chapa_secret_key,
            "chapa_webhook_secret": cfg.chapa_webhook_secret,
            "sheger_api_key": cfg.sheger_api_key,
            "sheger_api_url": cfg.sheger_api_url,
            "bank_accounts": cfg.bank_accounts,
            "manual_payment_instructions": cfg.manual_payment_instructions,
        }, status=status.HTTP_200_OK)

    def patch(self, request, *args, **kwargs):
        cfg = PaymentGatewayConfig.get_solo()
        data = request.data

        if "stripe_enabled" in data:
            cfg.stripe_enabled = bool(data["stripe_enabled"])
        if "chapa_enabled" in data:
            cfg.chapa_enabled = bool(data["chapa_enabled"])
        if "manual_bank_enabled" in data:
            cfg.manual_bank_enabled = bool(data["manual_bank_enabled"])
        if "manual_verification_mode" in data:
            mode = data["manual_verification_mode"]
            if mode in [PaymentGatewayConfig.VerificationMode.MANUAL_ADMIN,
                        PaymentGatewayConfig.VerificationMode.SHEGER_API]:
                cfg.manual_verification_mode = mode
        if "chapa_public_key" in data:
            cfg.chapa_public_key = str(data["chapa_public_key"]).strip()
        if "chapa_secret_key" in data:
            cfg.chapa_secret_key = str(data["chapa_secret_key"]).strip()
        if "chapa_webhook_secret" in data:
            cfg.chapa_webhook_secret = str(data["chapa_webhook_secret"]).strip()
        if "sheger_api_key" in data:
            cfg.sheger_api_key = str(data["sheger_api_key"]).strip()
        if "sheger_api_url" in data:
            cfg.sheger_api_url = str(data["sheger_api_url"]).strip()
        if "bank_accounts" in data and isinstance(data["bank_accounts"], list):
            cfg.bank_accounts = data["bank_accounts"]
        if "manual_payment_instructions" in data:
            cfg.manual_payment_instructions = str(data["manual_payment_instructions"])

        cfg.save()
        return Response({
            "message": "Payment gateway configuration updated successfully.",
            "config": {
                "stripe_enabled": cfg.stripe_enabled,
                "chapa_enabled": cfg.chapa_enabled,
                "manual_bank_enabled": cfg.manual_bank_enabled,
                "manual_verification_mode": cfg.manual_verification_mode,
                "chapa_public_key": cfg.chapa_public_key,
                "chapa_secret_key": cfg.chapa_secret_key,
                "sheger_api_key": cfg.sheger_api_key,
                "sheger_api_url": cfg.sheger_api_url,
                "bank_accounts": cfg.bank_accounts,
                "manual_payment_instructions": cfg.manual_payment_instructions,
            }
        }, status=status.HTTP_200_OK)


class AdminManualPaymentsView(APIView):
    """
    Lists payments for admin review.
    Query params: ?status=UNDER_REVIEW (default) or ?status=ALL
    """
    permission_classes = [IsAdminUserPermission]

    def get(self, request, *args, **kwargs):
        status_filter = request.query_params.get("status", "UNDER_REVIEW").upper()
        qs = Payment.objects.select_related("user", "order", "course").order_by("-created_at")
        if status_filter != "ALL":
            qs = qs.filter(status=status_filter)

        results = []
        for p in qs[:50]:
            student = p.user
            receipt_url = None
            if p.manual_receipt_image:
                try:
                    receipt_url = p.manual_receipt_image.url
                    if not receipt_url.startswith("http"):
                        receipt_url = request.build_absolute_uri(receipt_url)
                except Exception:
                    receipt_url = None

            item_title = "Lesson Booking"
            if p.course:
                item_title = f"Course: {p.course.title}"
            elif p.order:
                session_count = p.order.bookings.count()
                teacher_name = getattr(getattr(p.order.teacher, "user", None), "full_name", "")
                item_title = f"{session_count} Sessions with {teacher_name or 'Ustaz'}"

            results.append({
                "id": str(p.id),
                "tx_ref": p.tx_ref or "",
                "amount": str(p.amount),
                "currency": p.currency,
                "status": p.status,
                "payment_method": p.payment_method,
                "bank_name": p.bank_name or "Bank Transfer",
                "manual_transaction_id": p.manual_transaction_id or "",
                "manual_receipt_image": receipt_url,
                "verified_by": p.verified_by,
                "admin_note": p.admin_note or "",
                "item_title": item_title,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "student": {
                    "id": student.id if student else None,
                    "name": (student.full_name or student.username) if student else "Unknown",
                    "email": student.email if student else "",
                }
            })

        return Response({"payments": results, "count": len(results)}, status=status.HTTP_200_OK)


class AdminManualPaymentApproveView(APIView):
    """
    Approves a manual bank transfer / Telebirr payment.
    Activates the order/course atomically.
    """
    permission_classes = [IsAdminUserPermission]

    def post(self, request, payment_id, *args, **kwargs):
        try:
            payment = Payment.objects.get(id=payment_id)
        except Payment.DoesNotExist:
            return Response({"error": "Payment not found"}, status=status.HTTP_404_NOT_FOUND)

        if payment.status == Payment.Status.COMPLETED:
            return Response({"message": "Payment is already completed."}, status=status.HTTP_200_OK)

        note = request.data.get("note", f"Approved by admin {request.user.username} via mobile app")
        payment.verified_by = Payment.VerifiedBy.MANUAL_ADMIN
        payment.admin_note = note

        if payment.order:
            _confirm_order_payment(payment.order, payment, note=note)
        elif payment.course and payment.user:
            _confirm_course_enrollment(payment.course, payment.user, payment, note=note)
        else:
            payment.status = Payment.Status.COMPLETED
            payment.save(update_fields=["status", "verified_by", "admin_note", "updated_at"])

        return Response({
            "message": "Payment approved and activated successfully.",
            "payment_id": str(payment.id),
            "status": "COMPLETED",
        }, status=status.HTTP_200_OK)


class AdminManualPaymentRejectView(APIView):
    """
    Rejects a manual payment and notifies student.
    """
    permission_classes = [IsAdminUserPermission]

    def post(self, request, payment_id, *args, **kwargs):
        try:
            payment = Payment.objects.get(id=payment_id)
        except Payment.DoesNotExist:
            return Response({"error": "Payment not found"}, status=status.HTTP_404_NOT_FOUND)

        note = request.data.get("note", "Payment could not be verified.")
        payment.status = Payment.Status.FAILED
        payment.admin_note = note
        payment.save(update_fields=["status", "admin_note", "updated_at"])

        # Send notification to student
        if payment.user:
            Notification.objects.create(
                user=payment.user,
                title="Payment Reference Rejected",
                body=f"Your payment reference ({payment.manual_transaction_id or payment.tx_ref}) could not be verified: {note}. Please check with your bank or try another payment method.",
            )

        return Response({
            "message": "Payment rejected.",
            "payment_id": str(payment.id),
            "status": "FAILED",
        }, status=status.HTTP_200_OK)


class AdminShegerVerifyPaymentView(APIView):
    """
    Triggers automated verification of a payment using the ShegerPay API.
    Can verify by receipt screenshot OCR (/verify-image) or transaction reference (/verify).
    """
    permission_classes = [IsAdminUserPermission]

    def post(self, request, payment_id, *args, **kwargs):
        try:
            payment = Payment.objects.get(id=payment_id)
        except Payment.DoesNotExist:
            return Response({"error": "Payment not found"}, status=status.HTTP_404_NOT_FOUND)

        if payment.status == Payment.Status.COMPLETED:
            return Response({
                "message": "Payment is already completed.",
                "verified": True,
                "status": "COMPLETED",
                "payment_id": str(payment.id),
            }, status=status.HTTP_200_OK)

        result = ShegerService.verify_payment_record(payment, actor=request.user)
        return Response(result, status=status.HTTP_200_OK)


@method_decorator(csrf_exempt, name="dispatch")
class ShegerWebhookView(APIView):
    """
    Receives real-time payment.verified webhook notifications from ShegerPay.
    Verifies HMAC-SHA256 signature when secret is configured.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        payload_bytes = request.body
        sig_header = request.headers.get("X-ShegerPay-Signature", "")

        webhook_secret = getattr(settings, "SHEGERPAY_WEBHOOK_SECRET", "").strip()
        if webhook_secret and sig_header:
            import hmac
            import hashlib
            expected = hmac.new(webhook_secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
            provided = sig_header.replace("sha256=", "")
            if not hmac.compare_digest(expected, provided):
                logger.warning("ShegerPay webhook HMAC signature mismatch.")
                return Response({"error": "Invalid signature"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception:
            return Response({"error": "Invalid JSON"}, status=status.HTTP_400_BAD_REQUEST)

        event_type = payload.get("event")
        data = payload.get("data", {})
        logger.info(f"ShegerPay webhook event received: {event_type}")

        if event_type == "payment.verified":
            tx_id = data.get("transaction_id")
            ref_id = data.get("reference_id")

            payment = None
            if tx_id:
                payment = Payment.objects.filter(manual_transaction_id=tx_id).first()
            if not payment and ref_id:
                payment = Payment.objects.filter(tx_ref=ref_id).first()

            if payment and payment.status != Payment.Status.COMPLETED:
                note = f"Verified via ShegerPay webhook (Tx: {tx_id or ref_id})"
                payment.verified_by = Payment.VerifiedBy.SHEGER_API
                if payment.order:
                    _confirm_order_payment(payment.order, payment, note=note)
                elif payment.course and payment.user:
                    _confirm_course_enrollment(payment.course, payment.user, payment, note=note)
                else:
                    payment.status = Payment.Status.COMPLETED
                    payment.admin_note = note
                    payment.save(update_fields=["status", "verified_by", "admin_note", "updated_at"])

        return Response({"status": "received"}, status=status.HTTP_200_OK)

