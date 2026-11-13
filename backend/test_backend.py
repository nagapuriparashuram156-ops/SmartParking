import pytest
from datetime import datetime, timedelta
from fastapi import HTTPException
from database import db, TIMEZONE
from main import (
    home,
    health_check,
    get_slots,
    get_sensor_status,
    update_sensor_status,
    book_slot,
    list_bookings,
    get_booking,
    delete_booking,
    cancel_slot,
    SensorUpdateRequest,
    BookingCreateRequest,
    CancelSlotRequest,
)


@pytest.fixture(autouse=True)
def setup_database():
    """Ensure database tables and initial slots are freshly initialized."""
    db.init_db()


def test_home_endpoint():
    data = home()
    assert data["system"] == "Smart Parking System"
    assert data["status"] == "Online"
    assert data["timezone"] == "Asia/Kolkata"
    assert "endpoints" in data


def test_health_endpoint():
    data = health_check()
    assert data["status"] == "healthy"
    assert data["database"]["connected"] is True
    assert data["timezone"] == "Asia/Kolkata"


def test_get_slots_initial():
    data = get_slots()
    assert len(data["slots"]) == 3
    summary = data["summary"]
    assert summary["total_slots"] == 3
    assert summary["available_now"] == 3
    assert summary["physically_occupied"] == 0
    assert summary["booked_reserved"] == 0
    assert summary["sensor_errors"] == 0


def test_sensor_telemetry_flow():
    # 1. Update Slot 1 to Occupied (car parked)
    req = SensorUpdateRequest(slot_id=1, status="Occupied", distance_cm=8.5)
    res = update_sensor_status(req, _authorized=True)
    assert res["physical_status"] == "Occupied"
    assert res["distance_cm"] == 8.5

    # 2. Check dashboard reflection
    slots_data = get_slots()
    assert slots_data["summary"]["physically_occupied"] == 1
    assert slots_data["summary"]["available_now"] == 2
    assert slots_data["slots"][0]["status"] == "Occupied"

    # 3. Update Slot 1 back to Empty (car departs)
    req_empty = SensorUpdateRequest(slot_id=1, status="Empty", distance_cm=145.0)
    res_empty = update_sensor_status(req_empty, _authorized=True)
    assert res_empty["physical_status"] == "Empty"

    slots_after = get_slots()
    assert slots_after["summary"]["physically_occupied"] == 0
    assert slots_after["summary"]["available_now"] == 3
    assert slots_after["slots"][0]["status"] == "Available"


def test_sensor_error_state():
    # Update Slot 2 to Error (hardware disconnected / invalid reading)
    req = SensorUpdateRequest(slot_id=2, status="Error", distance_cm=None)
    update_sensor_status(req, _authorized=True)

    slots_data = get_slots()
    assert slots_data["summary"]["sensor_errors"] == 1
    assert slots_data["slots"][1]["status"] == "Sensor Error"
    # Ensure error slot is NOT counted as available
    assert slots_data["summary"]["available_now"] == 2

    # Reset back to Empty
    req_reset = SensorUpdateRequest(slot_id=2, status="Empty", distance_cm=200.0)
    update_sensor_status(req_reset, _authorized=True)


def test_booking_buffer_and_conflict_logic():
    now_kolkata = datetime.now(TIMEZONE)
    start_time = now_kolkata + timedelta(minutes=5)
    date_str = start_time.strftime("%Y-%m-%d")
    time_str = start_time.strftime("%H:%M")

    # 1. Book Slot 2 for 30 minutes
    book_res = book_slot(
        slot_id=2,
        body=BookingCreateRequest(
            date=date_str,
            start_time=time_str,
            duration_minutes=30,
        )
    )
    booking_id = book_res["booking"]["booking_id"]
    assert booking_id.startswith("BK-")

    # 2. Conflict: Overlapping reservation on Slot 2
    overlap_time = (start_time + timedelta(minutes=10)).strftime("%H:%M")
    with pytest.raises(HTTPException) as exc_overlap:
        book_slot(
            slot_id=2,
            body=BookingCreateRequest(
                date=date_str,
                start_time=overlap_time,
                duration_minutes=20,
            )
        )
    assert exc_overlap.value.status_code == 409
    assert "unavailable during this time" in exc_overlap.value.detail.lower()

    # 3. Buffer conflict: exactly at end time (needs 1-minute buffer)
    buffer_time = (start_time + timedelta(minutes=30)).strftime("%H:%M")
    with pytest.raises(HTTPException) as exc_buf:
        book_slot(
            slot_id=2,
            body=BookingCreateRequest(
                date=date_str,
                start_time=buffer_time,
                duration_minutes=20,
            )
        )
    assert exc_buf.value.status_code == 409

    # 4. Valid booking after 1-minute buffer (end + 1 min buffer = 31 min)
    valid_after_time = (start_time + timedelta(minutes=32)).strftime("%H:%M")
    valid_res = book_slot(
        slot_id=2,
        body=BookingCreateRequest(
            date=date_str,
            start_time=valid_after_time,
            duration_minutes=15,
        )
    )
    second_booking_id = valid_res["booking"]["booking_id"]
    assert second_booking_id.startswith("BK-")

    # 5. Same time on DIFFERENT slot (Slot 3) should succeed
    diff_slot_res = book_slot(
        slot_id=3,
        body=BookingCreateRequest(
            date=date_str,
            start_time=time_str,
            duration_minutes=30,
        )
    )
    assert diff_slot_res["booking"]["slot_id"] == 3

    # Clean up created bookings
    delete_booking(booking_id)
    delete_booking(second_booking_id)
    delete_booking(diff_slot_res["booking"]["booking_id"])


def test_past_time_booking_rejected():
    yesterday = datetime.now(TIMEZONE) - timedelta(days=1)
    with pytest.raises(HTTPException) as exc:
        book_slot(
            slot_id=1,
            body=BookingCreateRequest(
                date=yesterday.strftime("%Y-%m-%d"),
                start_time="10:00",
                duration_minutes=60,
            )
        )
    assert exc.value.status_code == 409
    assert "past" in exc.value.detail.lower()


def test_booking_cancellation():
    now_kolkata = datetime.now(TIMEZONE)
    future = now_kolkata + timedelta(hours=2)
    date_str = future.strftime("%Y-%m-%d")
    time_str = future.strftime("%H:%M")

    # Create a booking
    res = book_slot(
        slot_id=1,
        body=BookingCreateRequest(
            date=date_str,
            start_time=time_str,
            duration_minutes=45,
        )
    )
    bk_id = res["booking"]["booking_id"]

    # Verify in list
    all_bks = list_bookings(slot_id=1, status="ACTIVE")
    assert any(b["booking_id"] == bk_id for b in all_bks["bookings"])

    # Cancel by booking ID
    cancel_res = delete_booking(bk_id)
    assert cancel_res["booking"]["booking_status"] == "CANCELLED"

    # Confirm it is no longer returned under ACTIVE bookings
    active_bks = list_bookings(slot_id=1, status="ACTIVE")
    assert not any(b["booking_id"] == bk_id for b in active_bks["bookings"])
