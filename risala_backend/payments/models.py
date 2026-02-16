from django.db import models
from django.utils.translation import gettext_lazy as _
from risala_backend.utils.models import TimeStampedModel, UUIDModel
from risala_backend.users.models import BookingOrder

class Payment(TimeStampedModel, UUIDModel):
    """
    Represents a payment transaction for a booking order.
    """
    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        COMPLETED = "COMPLETED", _("Completed")
        FAILED = "FAILED", _("Failed")
        REFUNDED = "REFUNDED", _("Refunded")
        EXPIRED = "EXPIRED", _("Expired")

    order = models.OneToOneField(
        BookingOrder,
        on_delete=models.CASCADE,
        related_name="payment",
        help_text="The order this payment is for."
    )
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
        help_text="Stripe PaymentIntent ID (available after checkout)"
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
    
    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Payment {self.id} - {self.status} ({self.amount} {self.currency})"
