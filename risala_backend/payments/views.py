import stripe
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views import View
from rest_framework import status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from risala_backend.users.models import SessionBooking, BookingOrder, Notification
from risala_backend.payments.models import Payment
from risala_backend.courses.models import Course, Enrollment

stripe.api_key = settings.STRIPE_SECRET_KEY

class CreateCheckoutSessionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        import sys
        print(f"DEBUG: CreateCheckoutSessionView POST called", flush=True)
        print(f"DEBUG: Request Data: {request.data}", flush=True)

        order_id = request.data.get("order_id")
        booking_id = request.data.get("booking_id")
        course_id = request.data.get("course_id")

        # --- Course Payment Flow ---
        if course_id:
            return self._handle_course_payment(request, course_id)

        # --- New Package Flow (order_id) ---
        if order_id:
            return self._handle_order_payment(request, order_id)

        # --- Legacy Single-Booking Flow (booking_id) ---
        if booking_id:
            return self._handle_legacy_booking_payment(request, booking_id)

        # Neither provided
        print("DEBUG: Neither order_id, booking_id, nor course_id provided in request", flush=True)
        return Response(
            {"error": "order_id, booking_id, or course_id is required"},
            status=status.HTTP_400_BAD_REQUEST
        )

    def _handle_legacy_booking_payment(self, request, booking_id):
        """Wraps a single legacy booking into a BookingOrder, then pays."""
        from decimal import Decimal
        print(f"DEBUG: Legacy flow – wrapping booking {booking_id}", flush=True)
        try:
            booking = SessionBooking.objects.get(id=booking_id)
        except SessionBooking.DoesNotExist:
            return Response({"error": "Booking not found"}, status=status.HTTP_404_NOT_FOUND)

        if booking.student.user != request.user:
            return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)

        # If already linked to an order, just pay for that order
        if booking.order:
            return self._handle_order_payment(request, str(booking.order.id))

        # Create an order on-the-fly for this single booking
        duration = booking.end_at - booking.start_at
        hours = Decimal(str(duration.total_seconds() / 3600))
        rate = booking.hourly_rate or booking.teacher.hourly_rate or Decimal("10.00")
        total = max(hours * rate, Decimal("1.00"))

        with transaction.atomic():
            order = BookingOrder.objects.create(
                student=booking.student,
                teacher=booking.teacher,
                total_amount=total,
                currency="usd",
                status=BookingOrder.Status.PENDING,
            )
            booking.order = order
            booking.status = SessionBooking.Status.RESERVED
            booking.save(update_fields=["order", "status", "updated_at"])

        return self._handle_order_payment(request, str(order.id))

    def _handle_course_payment(self, request, course_id):
        """Core payment logic for purchasing a Course."""
        print(f"DEBUG: Creating checkout session for course {course_id}", flush=True)

        try:
            course = Course.objects.get(id=course_id)
        except Course.DoesNotExist:
            return Response({"error": "Course not found"}, status=status.HTTP_404_NOT_FOUND)

        if not hasattr(request.user, "student_profile"):
            return Response({"error": "Only students can enroll in courses."}, status=status.HTTP_403_FORBIDDEN)

        student = request.user.student_profile
        
        # Check if already enrolled
        if Enrollment.objects.filter(course=course, student=student).exists():
            return Response({"error": "Already enrolled in this course."}, status=status.HTTP_400_BAD_REQUEST)

        # Free course logic
        if course.price <= 0:
            Enrollment.objects.create(course=course, student=student, status=Enrollment.Status.ENROLLED)
            return Response({'sessionId': 'free_bypass', 'checkout_url': 'risala://payment/success?session_id=free_bypass'})

        # Paid course logic
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
            return Response({'sessionId': checkout_session.id, 'checkout_url': checkout_session.url})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _handle_order_payment(self, request, order_id):
        """Core payment logic for a BookingOrder."""
        print(f"DEBUG: Creating checkout session for order {order_id}", flush=True)

        try:
            order = BookingOrder.objects.get(id=order_id)
        except BookingOrder.DoesNotExist:
            print(f"DEBUG: Order {order_id} not found", flush=True)
            return Response({"error": "Order not found"}, status=status.HTTP_404_NOT_FOUND)

        # 1. Security Checks
        if order.student.user != request.user:
            return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)

        if order.status not in [BookingOrder.Status.PENDING, BookingOrder.Status.APPROVED]:
            return Response(
                {"error": f"Order is in {order.status} state, cannot be paid."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if Payment.objects.filter(order=order, status=Payment.Status.COMPLETED).exists():
            return Response({"error": "Order is already paid"}, status=status.HTTP_400_BAD_REQUEST)

        # 2. Amount
        amount = order.total_amount
        print(f"DEBUG: Amount for order {order_id}: {amount}", flush=True)
        if amount <= 0:
            return Response({"error": "Invalid order amount"}, status=status.HTTP_400_BAD_REQUEST)

        amount_cents = int(amount * 100)

        # 3. Create Stripe Checkout Session
        try:
            currency = getattr(settings, 'PAYMENT_DEFAULT_CURRENCY', 'usd')
            num_sessions = order.bookings.count()

            success_url = request.data.get("success_url", settings.STRIPE_SUCCESS_URL)
            cancel_url = request.data.get("cancel_url", settings.STRIPE_CANCEL_URL)

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
            print(f"DEBUG: Stripe session created: {checkout_session.id}", flush=True)
            print(f"DEBUG: success_url used: {settings.STRIPE_SUCCESS_URL}", flush=True)
            print(f"DEBUG: cancel_url used: {settings.STRIPE_CANCEL_URL}", flush=True)

            with transaction.atomic():
                Payment.objects.update_or_create(
                    order=order,
                    defaults={
                        'stripe_checkout_id': checkout_session.id,
                        'amount': amount,
                        'currency': currency,
                        'status': Payment.Status.PENDING
                    }
                )

            return Response({'sessionId': checkout_session.id, 'checkout_url': checkout_session.url})

        except Exception as e:
            import traceback
            print(f"ERROR: Stripe Checkout Session creation failed: {str(e)}", flush=True)
            traceback.print_exc()
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@method_decorator(csrf_exempt, name='dispatch')
class StripeWebhookView(View):
    def post(self, request, *args, **kwargs):
        payload = request.body
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
        print(f"DEBUG: Webhook endpoint hit!", flush=True)
        print(f"DEBUG: Signature header: {sig_header}", flush=True)
        print(f"DEBUG: Payload snippet: {payload[:100]}...", flush=True)
        
        event = None

        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
            )
        except ValueError as e:
            # Invalid payload
            print(f"ERROR: Webhook payload error: {e}", flush=True)
            return HttpResponse(status=400)
        except stripe.error.SignatureVerificationError as e:
            # Invalid signature
            print(f"ERROR: Webhook signature verification failed.", flush=True)
            print(f"ERROR: Secret used: {settings.STRIPE_WEBHOOK_SECRET[:10]}...", flush=True)
            return HttpResponse(status=400)
        except Exception as e:
            print(f"ERROR: Unexpected webhook error: {str(e)}", flush=True)
            return HttpResponse(status=400)

        print(f"DEBUG: Webhook event type: {event['type']}", flush=True)
        # Handle the event
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            print(f"DEBUG: Handling checkout.session.completed for session: {session.get('id')}", flush=True)
            self.handle_checkout_session_completed(session)
        elif event['type'] == 'checkout.session.expired':
            pass 

        return HttpResponse(status=200)

    def handle_checkout_session_completed(self, session):
        metadata = session.get('metadata', {})
        order_id = metadata.get('order_id')
        course_id = metadata.get('course_id')

        if course_id:
            self._process_course_purchase(session, course_id)
        elif order_id:
            self._process_order_purchase(session, order_id)

    def _process_course_purchase(self, session, course_id):
        try:
            course = Course.objects.get(id=course_id)
            user_id = session.get('client_reference_id')
            if not user_id:
                return

            from django.contrib.auth import get_user_model
            User = get_user_model()
            user = User.objects.get(id=user_id)
            if not hasattr(user, "student_profile"):
                return
                
            student = user.student_profile
            
            with transaction.atomic():
                enrollment, created = Enrollment.objects.get_or_create(
                    course=course,
                    student=student,
                    defaults={'status': Enrollment.Status.ENROLLED}
                )
                if created:
                    Notification.objects.create(
                        user=user,
                        title="Course Enrollment Successful",
                        body=f"You have successfully enrolled in {course.title}.",
                    )
                    if getattr(course.created_by, "user", None):
                        Notification.objects.create(
                            user=course.created_by.user,
                            title="New Course Student",
                            body=f"{user.full_name or user.username} enrolled in {course.title}.",
                        )
        except Exception as e:
            print(f"ERROR: Course webhook processing failed: {str(e)}")

    def _process_order_purchase(self, session, order_id):
        try:
            with transaction.atomic():
                order = BookingOrder.objects.select_for_update().get(id=order_id)
                
                # Check idempotency: If already paid, skip
                if order.status == BookingOrder.Status.PAID:
                    return

                # Update Payment
                payment, created = Payment.objects.get_or_create(order=order)
                payment.stripe_payment_intent_id = session.get('payment_intent')
                payment.status = Payment.Status.COMPLETED
                payment.save()

                # Update Order
                order.status = BookingOrder.Status.PAID
                order.save()

                # Update all linked Bookings
                order.bookings.all().update(status=SessionBooking.Status.CONFIRMED)
                
                # TODO: Trigger Notifications / Generate Meeting Link here
                teacher_user = getattr(order.teacher, "user", None)
                student_user = getattr(order.student, "user", None)
                if teacher_user and student_user:
                    num_sessions = order.bookings.count()
                    Notification.objects.create(
                        user=teacher_user,
                        title="Package Paid & Confirmed",
                        body=f"Student {student_user.full_name or student_user.username} has paid for {num_sessions} sessions.",
                    )
                
        except BookingOrder.DoesNotExist:
            print(f"ERROR: Webhook received for non-existent Order {order_id}")
        except Exception as e:
            print(f"ERROR: Webhook processing failed for Order {order_id}: {str(e)}")


class VerifyPaymentView(APIView):
    """
    Fallback endpoint to verify payment status if webhook is delayed/failed.
    The student's dashboard can call this using the session_id in the URL.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        session_id = request.data.get("session_id")
        if not session_id:
            return Response({"error": "session_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Retrieve session from Stripe
            session = stripe.checkout.Session.retrieve(session_id)
            
            if session.payment_status == "paid":
                # Trigger completion logic (idempotent)
                handler = StripeWebhookView()
                handler.handle_checkout_session_completed(session)
                return Response({"status": "paid", "message": "Payment verified and status updated."})
            else:
                return Response({"status": session.payment_status, "message": "Payment not completed yet."})

        except Exception as e:
            print(f"ERROR: Manual verification failed: {str(e)}", flush=True)
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
