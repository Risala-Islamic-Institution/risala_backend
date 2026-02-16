from django.urls import path
from risala_backend.payments.views import CreateCheckoutSessionView, StripeWebhookView, VerifyPaymentView

app_name = "payments"

urlpatterns = [
    path("create-session/", CreateCheckoutSessionView.as_view(), name="create_session"),
    path("checkout/", CreateCheckoutSessionView.as_view(), name="checkout"),
    path("webhook/", StripeWebhookView.as_view(), name="webhook"),
    path("verify-session/", VerifyPaymentView.as_view(), name="verify_session"),
]
