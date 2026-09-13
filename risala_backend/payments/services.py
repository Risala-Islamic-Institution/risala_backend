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
    Verifies bank/Telebirr transaction reference numbers automatically.
    """
    @classmethod
    def verify_transaction(
        cls,
        transaction_id: str,
        expected_amount: Decimal,
        bank_name: str = "",
    ) -> dict:
        """
        Calls the configured Sheger API endpoint to verify transaction authenticity.
        """
        config = PaymentGatewayConfig.get_solo()
        api_key = config.sheger_api_key.strip()
        api_url = (config.sheger_api_url or "https://api.shegerpay.com").strip().rstrip("/")

        if not api_key:
            logger.warning("Sheger API key not configured; cannot auto-verify transaction.")
            return {
                "verified": False,
                "error": "Sheger API credentials are not configured.",
                "pending_manual_review": True,
            }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "transaction_id": transaction_id,
            "amount": float(expected_amount),
            "bank": bank_name,
        }

        endpoint = f"{api_url}/api/v1/verify"
        try:
            response = requests.post(endpoint, json=payload, headers=headers, timeout=15)
            data = response.json()
            if response.status_code == 200 and data.get("verified") is True:
                return {
                    "verified": True,
                    "transaction_id": transaction_id,
                    "raw": data,
                }
            else:
                return {
                    "verified": False,
                    "error": data.get("message", "Transaction not verified by Sheger."),
                    "pending_manual_review": True,
                    "raw": data,
                }
        except Exception as e:
            logger.exception(f"Sheger verification network error: {e}")
            return {
                "verified": False,
                "error": f"Sheger network error: {str(e)}",
                "pending_manual_review": True,
            }
