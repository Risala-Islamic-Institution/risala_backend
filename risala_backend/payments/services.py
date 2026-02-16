from decimal import Decimal
from risala_backend.users.models import SessionBooking

def calculate_booking_price(booking: SessionBooking) -> Decimal:
    """
    Calculates the total price for a session booking based on the stored hourly_rate
    or the teacher's current hourly rate as fallback.
    """
    # Use booking's locked-in rate if available, otherwise fallback to teacher's rate
    hourly_rate = booking.hourly_rate
    if not hourly_rate or hourly_rate <= 0:
        hourly_rate = booking.teacher.hourly_rate
    
    # Still fallback to a test rate if absolutely nothing is set
    if not hourly_rate or hourly_rate <= 0:
        hourly_rate = Decimal("10.00")
    
    duration = booking.end_at - booking.start_at
    duration_minutes = Decimal(duration.total_seconds() / 60)
    
    price = (duration_minutes / Decimal(60)) * hourly_rate
    
    # Ensure minimum amount for Stripe (at least 1.00 USD)
    if price < Decimal("1.00"):
        price = Decimal("1.00")

    return price.quantize(Decimal("0.01"))
