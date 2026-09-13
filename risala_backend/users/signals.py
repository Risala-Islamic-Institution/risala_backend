import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from config.websocket import broadcast_slot_event
from risala_backend.users.models import TimeSlot, SessionBooking

logger = logging.getLogger(__name__)


@receiver(post_save, sender=TimeSlot)
def on_timeslot_saved(sender, instance, created, **kwargs):
    try:
        booked_by_user_id = None
        booked_by_student_id = None
        if instance.booking_id:
            try:
                booking = instance.booking
                if booking and booking.student:
                    booked_by_student_id = str(booking.student_id)
                    if booking.student.user_id:
                        booked_by_user_id = str(booking.student.user_id)
            except Exception:
                pass

        data = {
            "slot_id": str(instance.id),
            "teacher_id": str(instance.teacher_id),
            "start_time": instance.start_time.isoformat(),
            "end_time": instance.end_time.isoformat(),
            "duration_minutes": instance.duration_minutes,
            "is_booked": instance.is_booked,
            "allowed_booking_type": instance.allowed_booking_type,
            "booking_id": (
                str(instance.booking_id) if instance.booking_id else None
            ),
            "booked_by_user_id": booked_by_user_id,
            "booked_by_student_id": booked_by_student_id,
            "action": "created" if created else "updated",
        }
        broadcast_slot_event("slot_updated", data)
    except Exception as e:
        logger.debug("Error in on_timeslot_saved signal: %s", e)


@receiver(post_delete, sender=TimeSlot)
def on_timeslot_deleted(sender, instance, **kwargs):
    try:
        data = {
            "slot_id": str(instance.id),
            "teacher_id": str(instance.teacher_id),
            "action": "deleted",
        }
        broadcast_slot_event("slot_deleted", data)
    except Exception as e:
        logger.debug("Error in on_timeslot_deleted signal: %s", e)


@receiver(post_save, sender=SessionBooking)
def on_session_booking_saved(sender, instance, created, **kwargs):
    try:
        if hasattr(instance, "time_slot") and instance.time_slot:
            slot = instance.time_slot
            booked_by_user_id = None
            booked_by_student_id = str(instance.student_id) if instance.student_id else None
            if instance.student and instance.student.user_id:
                booked_by_user_id = str(instance.student.user_id)
            data = {
                "slot_id": str(slot.id),
                "teacher_id": str(slot.teacher_id),
                "start_time": slot.start_time.isoformat(),
                "end_time": slot.end_time.isoformat(),
                "duration_minutes": slot.duration_minutes,
                "is_booked": slot.is_booked,
                "booking_status": instance.status,
                "allowed_booking_type": slot.allowed_booking_type,
                "booking_id": str(instance.id),
                "booked_by_user_id": booked_by_user_id,
                "booked_by_student_id": booked_by_student_id,
                "action": "booking_updated",
            }
            broadcast_slot_event("slot_updated", data)
    except Exception as e:
        logger.debug("Error in on_session_booking_saved signal: %s", e)
