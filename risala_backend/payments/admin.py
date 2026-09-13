from django.contrib import admin
from django.db import transaction
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from risala_backend.payments.models import PaymentGatewayConfig, Payment
from risala_backend.users.models import BookingOrder, SessionBooking, Notification
from risala_backend.courses.models import Course, Enrollment

@admin.register(PaymentGatewayConfig)
class PaymentGatewayConfigAdmin(admin.ModelAdmin):
    list_display = [
        "__str__",
        "stripe_status_badge",
        "chapa_status_badge",
        "manual_bank_status_badge",
        "manual_verification_mode",
        "updated_at",
    ]
    fieldsets = [
        (
            _("Payment Method Toggles"),
            {
                "description": _("Activate or deactivate payment methods available to students."),
                "fields": (
                    "stripe_enabled",
                    "chapa_enabled",
                    "manual_bank_enabled",
                ),
            },
        ),
        (
            _("Manual / Bank Transfer & Telebirr Settings"),
            {
                "description": _(
                    "Configure bank accounts and verification mode for direct transfers."
                ),
                "fields": (
                    "manual_verification_mode",
                    "bank_accounts",
                    "manual_payment_instructions",
                ),
            },
        ),
        (
            _("Chapa API Credentials"),
            {
                "classes": ("collapse",),
                "description": _("Chapa payment gateway keys. Leave blank to fallback to environment variables."),
                "fields": (
                    "chapa_public_key",
                    "chapa_secret_key",
                    "chapa_webhook_secret",
                ),
            },
        ),
        (
            _("Sheger API Automated Verification"),
            {
                "classes": ("collapse",),
                "description": _("Automated verification of bank/Telebirr transaction IDs via Sheger API."),
                "fields": (
                    "sheger_api_key",
                    "sheger_api_url",
                ),
            },
        ),
    ]

    def has_add_permission(self, request):
        # Prevent creating multiple config objects (singleton)
        return not PaymentGatewayConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Stripe"))
    def stripe_status_badge(self, obj):
        color = "#10b981" if obj.stripe_enabled else "#ef4444"
        label = "Active" if obj.stripe_enabled else "Disabled"
        return format_html('<span style="color: {}; font-weight: bold;">● {}</span>', color, label)

    @admin.display(description=_("Chapa"))
    def chapa_status_badge(self, obj):
        color = "#10b981" if obj.chapa_enabled else "#ef4444"
        label = "Active" if obj.chapa_enabled else "Disabled"
        return format_html('<span style="color: {}; font-weight: bold;">● {}</span>', color, label)

    @admin.display(description=_("Bank Transfer"))
    def manual_bank_status_badge(self, obj):
        color = "#10b981" if obj.manual_bank_enabled else "#ef4444"
        label = "Active" if obj.manual_bank_enabled else "Disabled"
        return format_html('<span style="color: {}; font-weight: bold;">● {}</span>', color, label)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "user_display",
        "linked_item",
        "payment_method",
        "amount_display",
        "status_badge",
        "verified_by",
        "manual_transaction_id",
        "receipt_preview",
        "created_at",
    ]
    list_filter = [
        "status",
        "payment_method",
        "verified_by",
        "created_at",
    ]
    search_fields = [
        "id",
        "tx_ref",
        "manual_transaction_id",
        "stripe_checkout_id",
        "chapa_reference",
        "user__username",
        "user__email",
        "order__id",
    ]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "receipt_preview_large",
    ]
    fieldsets = [
        (
            _("Payment Overview"),
            {
                "fields": (
                    "id",
                    "user",
                    "order",
                    "course",
                    "payment_method",
                    "amount",
                    "currency",
                    "status",
                    "verified_by",
                )
            },
        ),
        (
            _("Manual Bank Transfer / Telebirr Verification"),
            {
                "description": _("Student transfer submission details and receipt proof."),
                "fields": (
                    "bank_name",
                    "manual_transaction_id",
                    "manual_receipt_image",
                    "receipt_preview_large",
                    "admin_note",
                ),
            },
        ),
        (
            _("Gateway Tracking & Metadata"),
            {
                "classes": ("collapse",),
                "fields": (
                    "tx_ref",
                    "stripe_checkout_id",
                    "stripe_payment_intent_id",
                    "chapa_reference",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    ]
    actions = ["approve_manual_payments", "reject_manual_payments", "verify_with_shegerpay"]

    @admin.display(description=_("User"))
    def user_display(self, obj):
        user = obj.user or (obj.order.student.user if obj.order and hasattr(obj.order, "student") else None)
        return user.email if user else "—"

    @admin.display(description=_("Item"))
    def linked_item(self, obj):
        if obj.order_id:
            return f"Order #{str(obj.order_id)[:8]}"
        if obj.course_id:
            return f"Course: {obj.course.title if obj.course else obj.course_id}"
        return "—"

    @admin.display(description=_("Amount"))
    def amount_display(self, obj):
        return f"{obj.amount} {obj.currency.upper()}"

    @admin.display(description=_("Status"))
    def status_badge(self, obj):
        colors = {
            Payment.Status.COMPLETED: "#10b981",
            Payment.Status.UNDER_REVIEW: "#f59e0b",
            Payment.Status.PENDING: "#6b7280",
            Payment.Status.FAILED: "#ef4444",
            Payment.Status.REFUNDED: "#8b5cf6",
        }
        color = colors.get(obj.status, "#6b7280")
        return format_html('<span style="background-color: {}; color: white; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 11px;">{}</span>', color, obj.status)

    @admin.display(description=_("Receipt"))
    def receipt_preview(self, obj):
        if obj.manual_receipt_image:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener noreferrer"><img src="{}" style="max-height: 40px; border-radius: 4px; border: 1px solid #ddd;" /></a>',
                obj.manual_receipt_image.url,
                obj.manual_receipt_image.url,
            )
        return "—"

    @admin.display(description=_("Receipt Full Inspection"))
    def receipt_preview_large(self, obj):
        if obj.manual_receipt_image:
            return format_html(
                '<div style="margin-top: 5px;">'
                '<a href="{}" target="_blank" rel="noopener noreferrer">'
                '<img src="{}" style="max-width: 500px; max-height: 500px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.15);" />'
                '</a><br/><small style="color: #666;">(Click image to view full resolution in new tab)</small>'
                '</div>',
                obj.manual_receipt_image.url,
                obj.manual_receipt_image.url,
            )
        return "No receipt uploaded"

    @admin.action(description=_("⚡ Verify Selected Payments with ShegerPay"))
    def verify_with_shegerpay(self, request, queryset):
        from risala_backend.payments.services import ShegerService
        verified_count = 0
        failed_count = 0

        for payment in queryset:
            if payment.status == Payment.Status.COMPLETED:
                continue
            res = ShegerService.verify_payment_record(payment, actor=request.user)
            if res.get("verified") is True:
                verified_count += 1
            else:
                failed_count += 1

        if verified_count:
            self.message_user(request, f"Successfully verified and activated {verified_count} payment(s) via ShegerPay.")
        if failed_count:
            self.message_user(request, f"{failed_count} payment(s) could not be verified automatically by ShegerPay (check transaction ID / receipt or approve manually).", level="warning")

    @admin.action(description=_("✓ Approve Selected Payments Manually (Confirm Orders/Enrollments)"))
    def approve_manual_payments(self, request, queryset):
        approved_count = 0
        for payment in queryset:
            if payment.status == Payment.Status.COMPLETED:
                continue

            with transaction.atomic():
                payment.status = Payment.Status.COMPLETED
                payment.verified_by = Payment.VerifiedBy.MANUAL_ADMIN
                payment.admin_note = f"Approved manually by {request.user.username}"
                payment.save()

                # 1. Booking Order Flow
                if payment.order:
                    order = payment.order
                    order.status = BookingOrder.Status.PAID
                    order.save(update_fields=["status", "updated_at"])
                    order.bookings.all().update(status=SessionBooking.Status.CONFIRMED)

                    # Notify teacher and student
                    teacher_user = getattr(order.teacher, "user", None)
                    student_user = getattr(order.student, "user", None)
                    if teacher_user and student_user:
                        Notification.objects.create(
                            user=student_user,
                            title="Payment Approved",
                            body=f"Your payment of {payment.amount} {payment.currency.upper()} was verified! Your lessons are confirmed.",
                        )
                        Notification.objects.create(
                            user=teacher_user,
                            title="Lesson Package Confirmed",
                            body=f"Student {student_user.full_name or student_user.username} payment was approved.",
                        )

                # 2. Course Purchase Flow
                if payment.course and payment.user:
                    if hasattr(payment.user, "student_profile"):
                        Enrollment.objects.get_or_create(
                            course=payment.course,
                            student=payment.user.student_profile,
                            defaults={"status": Enrollment.Status.ENROLLED}
                        )
                        Notification.objects.create(
                            user=payment.user,
                            title="Course Enrollment Confirmed",
                            body=f"Your payment for {payment.course.title} was approved! You can now access all course content.",
                        )

                approved_count += 1

        self.message_user(request, f"Successfully approved {approved_count} payment(s). Linked bookings/courses are now confirmed.")

    @admin.action(description=_("✕ Reject Selected Payments"))
    def reject_manual_payments(self, request, queryset):
        rejected_count = 0
        for payment in queryset:
            payment.status = Payment.Status.FAILED
            payment.admin_note = f"Rejected manually by {request.user.username}"
            payment.save(update_fields=["status", "admin_note", "updated_at"])

            user = payment.user or (payment.order.student.user if payment.order and hasattr(payment.order, "student") else None)
            if user:
                Notification.objects.create(
                    user=user,
                    title="Payment Rejected",
                    body="Your payment transfer could not be verified. Please check the transaction ID or contact support.",
                )
            rejected_count += 1

        self.message_user(request, f"Marked {rejected_count} payment(s) as FAILED.")


@admin.register(BookingOrder)
class BookingOrderAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "student_display",
        "teacher_display",
        "total_amount",
        "status_badge",
        "linked_payment_info",
        "created_at",
    ]
    list_filter = ["status", "created_at"]
    search_fields = ["id", "student__user__email", "student__user__full_name", "teacher__user__email"]
    readonly_fields = ["id", "created_at", "updated_at"]

    @admin.display(description=_("Student"))
    def student_display(self, obj):
        return obj.student.user.email if hasattr(obj, "student") and obj.student else "—"

    @admin.display(description=_("Teacher"))
    def teacher_display(self, obj):
        return obj.teacher.user.email if hasattr(obj, "teacher") and obj.teacher else "—"

    @admin.display(description=_("Status"))
    def status_badge(self, obj):
        color = "#10b981" if obj.status == BookingOrder.Status.PAID else "#f59e0b"
        return format_html('<span style="color: white; background: {}; padding: 2px 8px; border-radius: 4px; font-weight: bold;">{}</span>', color, obj.status)

    @admin.display(description=_("Payment & Slip"))
    def linked_payment_info(self, obj):
        payment = obj.payments.first()
        if not payment:
            return "No payment record"
        tx = payment.manual_transaction_id or payment.tx_ref or payment.status
        if payment.manual_receipt_image:
            return format_html(
                '<span>{}</span> | <a href="{}" target="_blank">View Receipt</a>',
                tx,
                payment.manual_receipt_image.url,
            )
        return tx

