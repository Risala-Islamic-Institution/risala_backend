from django.urls import path
from risala_backend.payments.views import (
    PaymentMethodsView,
    CreateCheckoutSessionView,
    StripeWebhookView,
    VerifyPaymentView,
    ChapaVerifyView,
    ChapaWebhookView,
    SubmitManualPaymentView,
    CancelOrRefundOrderView,
    AdminPaymentConfigView,
    AdminManualPaymentsView,
    AdminManualPaymentApproveView,
    AdminManualPaymentRejectView,
)

app_name = "payments"

urlpatterns = [
    # Gateway methods inquiry (used by mobile app to display active options)
    path("methods/", PaymentMethodsView.as_view(), name="methods"),
    
    # Online checkout session initialization (Stripe & Chapa)
    path("create-session/", CreateCheckoutSessionView.as_view(), name="create_session"),
    path("checkout/", CreateCheckoutSessionView.as_view(), name="checkout"),

    # Stripe verification and webhook
    path("verify-session/", VerifyPaymentView.as_view(), name="verify_session"),
    path("webhook/", StripeWebhookView.as_view(), name="webhook"),

    # Chapa verification and webhook
    path("chapa/verify/", ChapaVerifyView.as_view(), name="chapa_verify"),
    path("chapa/webhook/", ChapaWebhookView.as_view(), name="chapa_webhook"),

    # Manual Bank Transfer / Telebirr submission
    path("manual/submit/", SubmitManualPaymentView.as_view(), name="manual_submit"),

    # Order cancellation & refund
    path("cancel-order/", CancelOrRefundOrderView.as_view(), name="cancel_order"),

    # Admin App Endpoints (Mobile Admin Dashboard)
    path("admin/config/", AdminPaymentConfigView.as_view(), name="admin_config"),
    path("admin/manual-payments/", AdminManualPaymentsView.as_view(), name="admin_manual_payments"),
    path("admin/manual-payments/<str:payment_id>/approve/", AdminManualPaymentApproveView.as_view(), name="admin_approve_payment"),
    path("admin/manual-payments/<str:payment_id>/reject/", AdminManualPaymentRejectView.as_view(), name="admin_reject_payment"),
]
