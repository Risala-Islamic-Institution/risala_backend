from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from risala_backend.utils.models import TimeStampedModel, UUIDModel
from risala_backend.users.models import BookingOrder

class PaymentGatewayConfig(TimeStampedModel):
    """
    Singleton configuration for enabling/disabling payment methods,
    configuring credentials, and setting up manual bank/Telebirr details.
    """
    class VerificationMode(models.TextChoices):
        MANUAL_ADMIN = "MANUAL_ADMIN", _("Manual Admin Approval")
        SHEGER_API = "SHEGER_API", _("Automated Sheger API Verification")

    # Gateway Toggles
    stripe_enabled = models.BooleanField(
        default=True,
        help_text=_("Toggle Stripe (International cards) ON/OFF.")
    )
    chapa_enabled = models.BooleanField(
        default=True,
        help_text=_("Toggle Chapa (Telebirr, CBE Birr, Awash, local cards) ON/OFF.")
    )
    manual_bank_enabled = models.BooleanField(
        default=True,
        help_text=_("Toggle Manual / Direct Bank Transfer & Telebirr ON/OFF.")
    )

    # Verification Mode for Bank Transfers
    manual_verification_mode = models.CharField(
        max_length=30,
        choices=VerificationMode.choices,
        default=VerificationMode.MANUAL_ADMIN,
        help_text=_("Whether bank/Telebirr transfers require manual staff approval or automated Sheger API validation.")
    )

    # Chapa Credentials (falls back to settings/env if blank)
    chapa_public_key = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_("Chapa Public Key (CHAPUBK_...). Fallback to settings if blank.")
    )
    chapa_secret_key = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_("Chapa Secret Key (CHASECK_...). Fallback to settings if blank.")
    )
    chapa_webhook_secret = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_("Chapa Webhook Secret hash for verifying callback signatures.")
    )

    # Sheger API Credentials
    sheger_api_key = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_("API Key / Token for Sheger API automated transaction validation.")
    )
    sheger_api_url = models.CharField(
        max_length=255,
        blank=True,
        default="https://api.shegerpay.com",
        help_text=_("Base URL for Sheger verification service.")
    )

    # Bank Account details for manual bank transfer
    bank_accounts = models.JSONField(
        default=list,
        blank=True,
        help_text=_(
            "List of bank/telebirr accounts for manual transfers. "
            'Format: [{"bank_name": "CBE", "account_number": "1000...", "account_name": "Risala", "icon": "account_balance"}]'
        )
    )
    manual_payment_instructions = models.TextField(
        blank=True,
        default="Please transfer the exact amount to one of our official accounts, then enter your transaction reference number.",
        help_text=_("Instructions displayed to students on the Bank Transfer page.")
    )

    class Meta:
        verbose_name = _("Payment Gateway Configuration")
        verbose_name_plural = _("Payment Gateway Configuration")

    def __str__(self):
        stripe_st = "ON" if self.stripe_enabled else "OFF"
        chapa_st = "ON" if self.chapa_enabled else "OFF"
        bank_st = "ON" if self.manual_bank_enabled else "OFF"
        return f"Payment Gateways (Stripe: {stripe_st}, Chapa: {chapa_st}, Bank: {bank_st} [{self.manual_verification_mode}])"

    @classmethod
    def get_solo(cls):
        """
        Retrieves or creates the single configuration instance with default Ethiopian bank accounts.
        """
        config = cls.objects.first()
        if not config:
            config = cls.objects.create(
                id=1,
                stripe_enabled=True,
                chapa_enabled=True,
                manual_bank_enabled=True,
                manual_verification_mode=cls.VerificationMode.MANUAL_ADMIN,
                bank_accounts=[
                    {
                        "bank_name": "Commercial Bank of Ethiopia (CBE)",
                        "account_number": "1000123456789",
                        "account_name": "Risala Islamic Institution",
                        "icon": "account_balance"
                    },
                    {
                        "bank_name": "Telebirr",
                        "account_number": "0911000000",
                        "account_name": "Risala Islamic Institution",
                        "icon": "phone_android"
                    }
                ]
            )
        return config


class Payment(TimeStampedModel, UUIDModel):
    """
    Represents a payment transaction for a booking order or course purchase.
    """
    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        UNDER_REVIEW = "UNDER_REVIEW", _("Under Review")  # Awaiting manual admin approval or Sheger check
        COMPLETED = "COMPLETED", _("Completed")
        FAILED = "FAILED", _("Failed")
        REFUNDED = "REFUNDED", _("Refunded")
        EXPIRED = "EXPIRED", _("Expired")

    class PaymentMethod(models.TextChoices):
        STRIPE = "STRIPE", _("Stripe (Card)")
        CHAPA = "CHAPA", _("Chapa (Telebirr / CBE Birr)")
        MANUAL_BANK = "MANUAL_BANK", _("Bank Transfer / Telebirr")

    class VerifiedBy(models.TextChoices):
        WEBHOOK = "WEBHOOK", _("Automated Webhook")
        MANUAL_ADMIN = "MANUAL_ADMIN", _("Manual Admin Approval")
        SHEGER_API = "SHEGER_API", _("Sheger API")
        AUTO_BYPASS = "AUTO_BYPASS", _("Free / Auto Bypass")

    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.STRIPE,
        help_text=_("The payment method/gateway used.")
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
        help_text=_("User who made the payment.")
    )

    order = models.ForeignKey(
        BookingOrder,
        on_delete=models.CASCADE,
        related_name="payments",
        null=True,
        blank=True,
        help_text=_("The order this payment is for (if booking sessions).")
    )

    course = models.ForeignKey(
        "courses.Course",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
        help_text=_("The course this payment is for (if purchasing a course).")
    )

    # Universal transaction reference for Chapa / Sheger tracking
    tx_ref = models.CharField(
        max_length=255,
        unique=True,
        blank=True,
        null=True,
        help_text=_("Unique transaction reference for tracking across gateways.")
    )

    # Stripe fields
    stripe_checkout_id = models.CharField(
        max_length=255, 
        unique=True, 
        blank=True, 
        null=True,
        help_text="Stripe Checkout Session ID"
    )
    stripe_payment_intent_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Stripe PaymentIntent ID"
    )

    # Chapa fields
    chapa_reference = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Chapa transaction reference")
    )

    # Manual Bank Transfer / Telebirr fields
    manual_transaction_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Transaction ID / Reference entered by customer for bank/Telebirr transfer.")
    )
    manual_receipt_image = models.ImageField(
        upload_to="payments/receipts/%Y/%m/",
        blank=True,
        null=True,
        help_text=_("Uploaded transfer receipt screenshot.")
    )
    bank_name = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text=_("Bank or wallet name used (e.g. CBE, Telebirr).")
    )

    amount = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        help_text="Amount in the currency (e.g., 50.00)"
    )
    currency = models.CharField(max_length=10, default="etb")
    status = models.CharField(
        max_length=20, 
        choices=Status.choices, 
        default=Status.PENDING
    )

    verified_by = models.CharField(
        max_length=20,
        choices=VerifiedBy.choices,
        default=VerifiedBy.WEBHOOK,
        help_text=_("How this payment was verified.")
    )
    admin_note = models.TextField(
        blank=True,
        default="",
        help_text=_("Staff notes for manual approval or rejection.")
    )
    
    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        item = f"Order {self.order_id}" if self.order_id else (f"Course {self.course_id}" if self.course_id else "No linked item")
        return f"Payment {self.id} [{self.payment_method}] - {self.status} ({self.amount} {self.currency}) for {item}"
