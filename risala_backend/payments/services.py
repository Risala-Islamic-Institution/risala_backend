import json
import logging
import uuid
import requests
from decimal import Decimal
from django.conf import settings
from risala_backend.users.models import SessionBooking
from risala_backend.payments.models import PaymentGatewayConfig

logger = logging.getLogger(__name__)

def calculate_booking_price(booking: SessionBooking) -> Decimal:
    """
    Calculates the total price for a session booking based on the stored hourly_rate
    or the teacher's current hourly rate as fallback.
    """
    hourly_rate = booking.hourly_rate
    if not hourly_rate or hourly_rate <= 0:
        hourly_rate = booking.teacher.hourly_rate
    
    if not hourly_rate or hourly_rate <= 0:
        hourly_rate = Decimal("10.00")
    
    duration = booking.end_at - booking.start_at
    duration_minutes = Decimal(duration.total_seconds() / 60)
    price = (duration_minutes / Decimal(60)) * hourly_rate
    
    if price < Decimal("1.00"):
        price = Decimal("1.00")

    return price.quantize(Decimal("0.01"))


class ChapaService:
    """
    Handles payment initialization and verification with the Chapa API.
    Supports Telebirr, CBE Birr, Awash Birr, and local Ethiopian payment methods.
    """
    BASE_URL = "https://api.chapa.co/v1"

    @classmethod
    def get_secret_key(cls) -> str:
        config = PaymentGatewayConfig.get_solo()
        if config.chapa_secret_key:
            return config.chapa_secret_key.strip()
        return getattr(settings, "CHAPA_SECRET_KEY", "").strip()

    @classmethod
    def get_public_key(cls) -> str:
        config = PaymentGatewayConfig.get_solo()
        if config.chapa_public_key:
            return config.chapa_public_key.strip()
        return getattr(settings, "CHAPA_PUBLIC_KEY", "").strip()

    @classmethod
    def initialize_transaction(
        cls,
        amount: Decimal,
        currency: str,
        email: str,
        first_name: str,
        last_name: str,
        tx_ref: str,
        callback_url: str,
        return_url: str,
        title: str = "Risala Payment",
        description: str = "Lesson or Course Payment",
    ) -> dict:
        """
        Calls Chapa /transaction/initialize to generate a hosted checkout URL.
        Returns dict with status and checkout_url.
        """
        secret_key = cls.get_secret_key()
        if not secret_key:
            raise ValueError("Chapa Secret Key is not configured in Admin or Settings.")

        headers = {
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "amount": str(amount),
            "currency": currency.upper(),
            "email": email or "customer@risala.com",
            "first_name": first_name or "Student",
            "last_name": last_name or ".",
            "tx_ref": tx_ref,
            "callback_url": callback_url,
            "return_url": return_url,
            "customization": {
                "title": title,
                "description": description,
            },
        }

        endpoint = f"{cls.BASE_URL}/transaction/initialize"
        logger.info(f"Initializing Chapa payment for tx_ref: {tx_ref}, amount: {amount} {currency}")
        
        response = requests.post(endpoint, json=payload, headers=headers, timeout=20)
        data = response.json()

        if response.status_code == 200 and data.get("status") == "success":
            checkout_url = data.get("data", {}).get("checkout_url")
            return {
                "success": True,
                "checkout_url": checkout_url,
                "tx_ref": tx_ref,
                "raw": data,
            }
        else:
            err_msg = data.get("message", "Chapa initialization failed.")
            logger.error(f"Chapa initialization error: {err_msg}, status: {response.status_code}")
            return {
                "success": False,
                "error": err_msg,
                "raw": data,
            }

    @classmethod
    def verify_transaction(cls, tx_ref: str) -> dict:
        """
        Authoritatively verifies a transaction reference against Chapa's servers.
        Returns dict with is_paid (bool), amount, currency, and reference.
        """
        secret_key = cls.get_secret_key()
        if not secret_key:
            raise ValueError("Chapa Secret Key is not configured.")

        headers = {
            "Authorization": f"Bearer {secret_key}",
        }
        endpoint = f"{cls.BASE_URL}/transaction/verify/{tx_ref}"
        
        try:
            response = requests.get(endpoint, headers=headers, timeout=20)
            data = response.json()
            if response.status_code == 200 and data.get("status") == "success":
                tx_data = data.get("data", {})
                chapa_status = tx_data.get("status", "").lower()
                is_paid = (chapa_status == "success")
                amount = Decimal(str(tx_data.get("amount", 0)))
                currency = tx_data.get("currency", "ETB")
                reference = tx_data.get("reference") or tx_data.get("transaction_id")
                return {
                    "success": True,
                    "is_paid": is_paid,
                    "amount": amount,
                    "currency": currency,
                    "reference": reference,
                    "raw": data,
                }
            else:
                return {
                    "success": False,
                    "is_paid": False,
                    "error": data.get("message", "Verification failed"),
                    "raw": data,
                }
        except Exception as e:
            logger.exception(f"Exception during Chapa verification for {tx_ref}: {e}")
            return {
                "success": False,
                "is_paid": False,
                "error": str(e),
            }


class ShegerService:
    """
    Automated transaction verification via Sheger API.
    Strictly follows official ShegerPay API specification (v2.5.0):
    - Base URL: https://api.shegerpay.com/api/v1
    - Auth: X-API-Key header (supported: test, live, or demo keys)
    - Supports JSON transaction verification (/verify)
    - Supports OCR Receipt Image verification (/verify-image)
    - Auto-normalizes Ethiopian providers (cbe, telebirr, boa, awash, dashen, ebirr_kaafi, ebirr_coop, cbebirr, mpesa)
    """
    BASE_URL = "https://api.shegerpay.com/api/v1"

    PROVIDER_MAP = {
        "cbe": "cbe",
        "commercial bank of ethiopia": "cbe",
        "commercial bank of ethiopia (cbe)": "cbe",
        "telebirr": "telebirr",
        "tele birr": "telebirr",
        "boa": "boa",
        "bank of abyssinia": "boa",
        "abyssinia": "boa",
        "awash": "awash",
        "awash bank": "awash",
        "dashen": "dashen",
        "dashen bank": "dashen",
        "ebirr": "ebirr_kaafi",
        "ebirr kaafi": "ebirr_kaafi",
        "kaafi": "ebirr_kaafi",
        "coop": "ebirr_coop",
        "cooperative bank of oromia": "ebirr_coop",
        "coopay": "ebirr_coop",
        "cbebirr": "cbebirr",
        "cbe birr": "cbebirr",
        "mpesa": "mpesa",
        "m-pesa": "mpesa",
    }

    @classmethod
    def get_api_key(cls) -> str:
        import os
        # 1. Environment variable (Render Environment Variables / System)
        env_key = os.environ.get("SHEGERPAY_API_KEY", "").strip()
        if env_key:
            return env_key
        # 2. Django settings
        settings_key = getattr(settings, "SHEGERPAY_API_KEY", "")
        if settings_key and str(settings_key).strip():
            return str(settings_key).strip()
        # 3. Dynamic database configuration (PaymentGatewayConfig)
        try:
            config = PaymentGatewayConfig.get_solo()
            if config.sheger_api_key and config.sheger_api_key.strip():
                return config.sheger_api_key.strip()
        except Exception:
            pass
        return ""

    @classmethod
    def get_base_url(cls) -> str:
        config = PaymentGatewayConfig.get_solo()
        url = (config.sheger_api_url or cls.BASE_URL).strip().rstrip("/")
        if not url.endswith("/api/v1"):
            url = f"{url}/api/v1"
        return url

    @classmethod
    def normalize_provider(cls, bank_name: str) -> str:
        if not bank_name:
            return "cbe"
        normalized = bank_name.strip().lower()
        for key, val in cls.PROVIDER_MAP.items():
            if key in normalized:
                return val
        return "cbe"

    @classmethod
    def verify_transaction(
        cls,
        transaction_id: str,
        expected_amount: Decimal,
        bank_name: str = "",
        expected_sender_name: str = "",
    ) -> dict:
        """
        Calls POST /api/v1/verify
        """
        api_key = cls.get_api_key()
        if not api_key:
            logger.warning("ShegerPay API key not configured; cannot auto-verify transaction.")
            return {
                "verified": False,
                "error": "ShegerPay API key is not configured.",
                "pending_manual_review": True,
            }

        provider = cls.normalize_provider(bank_name)
        headers = {
            "X-API-Key": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "provider": provider,
            "transaction_id": transaction_id.strip(),
            "amount": float(expected_amount),
            "merchant_name": "Risala",
        }
        if expected_sender_name:
            payload["expected_sender_name"] = expected_sender_name

        endpoint = f"{cls.get_base_url()}/verify"
        logger.info(f"Calling ShegerPay /verify for provider={provider}, tx={transaction_id}, amount={expected_amount}")

        try:
            response = requests.post(endpoint, json=payload, headers=headers, timeout=20)
            try:
                data = response.json()
            except Exception:
                data = {"raw_text": response.text}

            is_verified = (
                response.status_code == 200 and (
                    data.get("verified") is True or
                    data.get("valid") is True or
                    data.get("status") == "verified"
                )
            )

            if is_verified:
                return {
                    "verified": True,
                    "status": "verified",
                    "transaction_id": transaction_id,
                    "reference_id": data.get("reference_id") or data.get("request_id") or transaction_id,
                    "provider": provider,
                    "raw": data,
                }
            else:
                reason = data.get("reason") or data.get("message") or data.get("error") or "Transaction could not be verified by ShegerPay."
                provider_status = data.get("provider_status") or data.get("status") or "unverified"
                return {
                    "verified": False,
                    "status": provider_status,
                    "reason": reason,
                    "error": reason,
                    "pending_manual_review": True,
                    "raw": data,
                }
        except Exception as e:
            logger.exception(f"ShegerPay network exception: {e}")
            return {
                "verified": False,
                "status": "network_error",
                "error": f"ShegerPay network connection error: {str(e)}",
                "pending_manual_review": True,
            }

    @classmethod
    def verify_receipt_image(
        cls,
        image_file_or_path,
        expected_amount: Decimal,
        bank_name: str = "",
        expected_sender_name: str = "",
    ) -> dict:
        """
        Calls POST /api/v1/verify-image with OCR auto-detect.
        """
        api_key = cls.get_api_key()
        if not api_key:
            return {
                "verified": False,
                "error": "ShegerPay API key is not configured.",
                "pending_manual_review": True,
            }

        provider = cls.normalize_provider(bank_name) if bank_name else ""
        headers = {
            "X-API-Key": api_key,
            "Authorization": f"Bearer {api_key}",
        }

        data_fields = {
            "amount": str(float(expected_amount)),
            "merchant_name": "Risala",
        }
        if provider:
            data_fields["provider"] = provider
        if expected_sender_name:
            data_fields["expected_sender_name"] = expected_sender_name

        endpoint = f"{cls.get_base_url()}/verify-image"
        logger.info(f"Calling ShegerPay /verify-image with amount={expected_amount}, provider={provider}")

        try:
            files = {}
            if hasattr(image_file_or_path, "read"):
                # In-memory or Django File object
                image_file_or_path.seek(0)
                filename = getattr(image_file_or_path, "name", "receipt.jpg")
                files["screenshot"] = (filename, image_file_or_path.read(), "image/jpeg")
            elif isinstance(image_file_or_path, str):
                import os
                if os.path.exists(image_file_or_path):
                    with open(image_file_or_path, "rb") as f:
                        files["screenshot"] = (os.path.basename(image_file_or_path), f.read(), "image/jpeg")

            if not files:
                return {
                    "verified": False,
                    "error": "No valid receipt file data to upload.",
                    "pending_manual_review": True,
                }

            response = requests.post(endpoint, data=data_fields, files=files, headers=headers, timeout=30)
            try:
                data = response.json()
            except Exception:
                data = {"raw_text": response.text}

            is_verified = (
                response.status_code == 200 and (
                    data.get("verified") is True or
                    data.get("valid") is True or
                    data.get("status") == "verified"
                )
            )

            if is_verified:
                ref = data.get("reference_id") or data.get("transaction_id") or data.get("request_id")
                return {
                    "verified": True,
                    "status": "verified",
                    "reference_id": ref,
                    "transaction_id": data.get("transaction_id") or ref,
                    "provider": data.get("provider") or provider,
                    "raw": data,
                }
            else:
                reason = data.get("reason") or data.get("message") or data.get("error") or "Receipt could not be verified by ShegerPay OCR."
                return {
                    "verified": False,
                    "status": data.get("status", "unverified"),
                    "reason": reason,
                    "error": reason,
                    "pending_manual_review": True,
                    "raw": data,
                }
        except Exception as e:
            logger.exception(f"ShegerPay verify-image exception: {e}")
            return {
                "verified": False,
                "status": "network_error",
                "error": f"ShegerPay OCR connection error: {str(e)}",
                "pending_manual_review": True,
            }

    @classmethod
    def verify_payment_record(cls, payment, actor=None) -> dict:
        """
        High-level verification for a Payment model instance.
        Runs verify_receipt_image if receipt uploaded, or verify_transaction if transaction ID present.
        """
        sender_name = ""
        user = payment.user or (payment.order.student.user if payment.order and hasattr(payment.order, "student") else None)
        if user:
            sender_name = user.full_name or user.username

        result = {"verified": False, "error": "No transaction ID or receipt uploaded."}

        # 1. First priority: receipt image verification if available (especially essential for CBE)
        if payment.manual_receipt_image:
            try:
                result = cls.verify_receipt_image(
                    image_file_or_path=payment.manual_receipt_image.file,
                    expected_amount=payment.amount,
                    bank_name=payment.bank_name,
                    expected_sender_name=sender_name,
                )
            except Exception as e:
                logger.warning(f"Failed to read manual_receipt_image file: {e}")

        # 2. If not verified yet and manual_transaction_id exists, try /verify
        if not result.get("verified") and payment.manual_transaction_id and payment.manual_transaction_id.strip():
            result = cls.verify_transaction(
                transaction_id=payment.manual_transaction_id.strip(),
                expected_amount=payment.amount,
                bank_name=payment.bank_name,
                expected_sender_name=sender_name,
            )

        # Record ShegerPay telemetry on payment model
        is_verified = result.get("verified") is True
        payment.sheger_status = "verified" if is_verified else (result.get("status") or "failed")
        payment.sheger_reason = result.get("reason") or result.get("error") or ("Verified by ShegerPay" if is_verified else "Verification failed")
        if isinstance(result.get("raw"), dict):
            payment.sheger_response = result.get("raw")

        # 3. If verified, update the payment model and activate associated orders atomically
        if is_verified:
            from risala_backend.payments.models import Payment
            from risala_backend.payments.views import _confirm_order_payment, _confirm_course_enrollment

            ref = result.get("reference_id") or payment.manual_transaction_id or ""
            note = f"Auto-verified via ShegerPay (Ref: {ref})"
            payment.verified_by = Payment.VerifiedBy.SHEGER_API
            if result.get("transaction_id") and not payment.manual_transaction_id:
                payment.manual_transaction_id = result.get("transaction_id")

            if payment.order:
                _confirm_order_payment(payment.order, payment, note=note)
            elif payment.course and payment.user:
                _confirm_course_enrollment(payment.course, payment.user, payment, note=note)
            else:
                payment.status = Payment.Status.COMPLETED
                payment.admin_note = note
                payment.save(update_fields=["status", "verified_by", "admin_note", "manual_transaction_id", "sheger_status", "sheger_reason", "sheger_response", "updated_at"])

            result["status"] = "COMPLETED"
            result["payment_id"] = str(payment.id)
            result["message"] = "Payment successfully verified by ShegerPay! Linked sessions/course confirmed."
        else:
            payment.save(update_fields=["sheger_status", "sheger_reason", "sheger_response", "updated_at"])

        return result

