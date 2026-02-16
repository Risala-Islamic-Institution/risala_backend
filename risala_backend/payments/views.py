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

stripe.api_key = settings.STRIPE_SECRET_KEY

class CreateCheckoutSessionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        import sys
        print(f"DEBUG: CreateCheckoutSessionView POST called", flush=True)
        print(f"DEBUG: Request Data: {request.data}", flush=True)

        order_id = request.data.get("order_id")
        booking_id = request.data.get("booking_id")

        # --- New Package Flow (order_id) ---
        if order_id:
            return self._handle_order_payment(request, order_id)

        # --- Legacy Single-Booking Flow (booking_id) ---
        if booking_id:
            return self._handle_legacy_booking_payment(request, booking_id)

        # Neither provided
        print("DEBUG: Neither order_id nor booking_id provided in request", flush=True)
        return Response(
            {"error": "order_id or booking_id is required"},
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

            checkout_session = stripe.checkout.Session.create(
                payment_method_types=['card'],
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
                success_url=settings.STRIPE_SUCCESS_URL,
                cancel_url=settings.STRIPE_CANCEL_URL,
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
        order_id = session.get('metadata', {}).get('order_id')
        if not order_id:
            return

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
