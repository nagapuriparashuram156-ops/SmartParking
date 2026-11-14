import pytest
from datetime import datetime, timedelta
from fastapi import HTTPException
from database import db, TIMEZONE, get_current_time
from main import (
    home,
    health_check,
    get_slots,
    get_slot_availability_endpoint,
    get_sensor_status,
    update_sensor_status,
    book_slot,
    create_booking_direct,
    list_bookings,
    get_booking,
    delete_booking,
    cancel_slot,
    SensorUpdateRequest,
    BookingCreateRequest,
    BookingCreateRequestWithSlot,
    CancelSlotRequest,
)


@pytest.fixture(autouse=True)
def setup_database():
    """Ensure database tables and initial slots are freshly initialized and isolated before every test."""
    db.init_db()
    conn, db_type = db.get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM bookings;")
        now_str = get_current_time().strftime("%Y-%m-%d %H:%M:%S")
        if db_type == "mysql":
            cursor.execute("UPDATE sensor_status SET physical_status = 'Empty', distance_cm = 200.0, is_healthy = 1, last_heartbeat = NOW();")
        else:
            cursor.execute("UPDATE sensor_status SET physical_status = 'Empty', distance_cm = 200.0, is_healthy = 1, last_heartbeat = ?;", (now_str,))
        conn.commit()
    finally:
        cursor.close()
        conn.close()


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


# ====================================================================
# TEST 1: All three slots empty
# ====================================================================
def test_01_all_three_slots_empty():
    for slot_id in (1, 2, 3):
        update_sensor_status(SensorUpdateRequest(slot_id=slot_id, status="Empty", distance_cm=180.0), _authorized=True)

    data = get_slots()
    summary = data["summary"]
    assert summary["total_slots"] == 3
    assert summary["physically_occupied"] == 0
    assert summary["physical_available"] == 3
    assert summary["booked_reserved"] == 0
    assert summary["available_now"] == 3
    assert summary["sensor_errors"] == 0


# ====================================================================
# TEST 2: One vehicle detected in Slot 1
# ====================================================================
def test_02_one_vehicle_detected_slot1():
    update_sensor_status(SensorUpdateRequest(slot_id=1, status="Occupied", distance_cm=8.5), _authorized=True)

    data = get_slots()
    summary = data["summary"]
    assert summary["physically_occupied"] == 1
    assert summary["physical_available"] == 2
    assert summary["available_now"] == 2
    assert data["slots"][0]["status"] == "Occupied"
    assert data["slots"][0]["is_physically_occupied"] is True


# ====================================================================
# TEST 3: Vehicles detected in Slots 1 and 2
# ====================================================================
def test_03_vehicles_detected_slots_1_and_2():
    update_sensor_status(SensorUpdateRequest(slot_id=1, status="Occupied", distance_cm=8.0), _authorized=True)
    update_sensor_status(SensorUpdateRequest(slot_id=2, status="Occupied", distance_cm=9.2), _authorized=True)

    data = get_slots()
    summary = data["summary"]
    assert summary["physically_occupied"] == 2
    assert summary["physical_available"] == 1
    assert summary["available_now"] == 1


# ====================================================================
# TEST 4: All three slots occupied
# ====================================================================
def test_04_all_three_slots_occupied():
    for slot_id in (1, 2, 3):
        update_sensor_status(SensorUpdateRequest(slot_id=slot_id, status="Occupied", distance_cm=7.0), _authorized=True)

    data = get_slots()
    summary = data["summary"]
    assert summary["physically_occupied"] == 3
    assert summary["physical_available"] == 0
    assert summary["available_now"] == 0


# ====================================================================
# TEST 5: Vehicles removed
# ====================================================================
def test_05_vehicles_removed():
    for slot_id in (1, 2, 3):
        update_sensor_status(SensorUpdateRequest(slot_id=slot_id, status="Occupied", distance_cm=7.0), _authorized=True)

    for slot_id in (1, 2, 3):
        update_sensor_status(SensorUpdateRequest(slot_id=slot_id, status="Empty", distance_cm=180.0), _authorized=True)

    data = get_slots()
    summary = data["summary"]
    assert summary["physically_occupied"] == 0
    assert summary["physical_available"] == 3
    assert summary["available_now"] == 3


# ====================================================================
# TEST 6: Upcoming booking displayed
# ====================================================================
def test_06_upcoming_booking_displayed():
    now = get_current_time()
    future_start = now + timedelta(hours=2)
    date_str = future_start.strftime("%Y-%m-%d")
    time_str = future_start.strftime("%H:%M")

    book_slot(
        slot_id=1,
        body=BookingCreateRequest(date=date_str, start_time=time_str, duration_minutes=60)
    )

    data = get_slots()
    slot1 = data["slots"][0]
    assert slot1["has_upcoming_reservation"] is True
    assert slot1["status"] == "Upcoming Reservation"
    assert "Available now until" in slot1["availability_window"]


# ====================================================================
# TEST 7: Upcoming booking counted in BOOKED / RESERVED KPI
# (Verifies user reported bug where at 07:22 an 08:00 booking was not counted)
# ====================================================================
def test_07_upcoming_booking_counted_in_booked_reserved_kpi():
    now = get_current_time()
    future_start = now + timedelta(hours=1)
    date_str = future_start.strftime("%Y-%m-%d")
    time_str = future_start.strftime("%H:%M")

    book_slot(
        slot_id=1,
        body=BookingCreateRequest(date=date_str, start_time=time_str, duration_minutes=60)
    )

    data = get_slots()
    summary = data["summary"]
    # Section 2 Specification: upcoming booking MUST be counted in BOOKED / RESERVED KPI!
    assert summary["booked_reserved"] == 1
    assert summary["total_slots"] == 3
    assert summary["physical_available"] == 3
    assert summary["physically_occupied"] == 0
    assert summary["available_now"] == 3, "Bay is empty and available for parking right now before 08:00"


# ====================================================================
# TEST 8: Active booking counted in KPI
# ====================================================================
def test_08_active_booking_counted_in_kpi():
    now = get_current_time()
    start_dt = now - timedelta(minutes=10)
    end_dt = now + timedelta(minutes=50)

    conn, db_type = db.get_connection()
    cursor = conn.cursor()
    try:
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT INTO bookings (
                booking_id, slot_id, booking_date, start_time, end_time,
                start_datetime, end_datetime, duration_minutes, booking_status,
                created_at, updated_at
            ) VALUES (?, 1, ?, ?, ?, ?, ?, 60, 'ACTIVE', ?, ?);
        """ if db_type == "sqlite" else """
            INSERT INTO bookings (
                booking_id, slot_id, booking_date, start_time, end_time,
                start_datetime, end_datetime, duration_minutes, booking_status,
                created_at, updated_at
            ) VALUES (%s, 1, %s, %s, %s, %s, %s, 60, 'ACTIVE', %s, %s);
        """, (
            "BK-ACT8",
            start_dt.strftime("%Y-%m-%d"),
            start_dt.strftime("%H:%M:00"),
            end_dt.strftime("%H:%M:00"),
            start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            now_str,
            now_str
        ))
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    data = get_slots()
    summary = data["summary"]
    assert summary["booked_reserved"] == 1
    assert data["slots"][0]["is_currently_reserved"] is True
    assert data["slots"][0]["status"] == "Reserved"


# ====================================================================
# TEST 9: Cancelled booking excluded from KPI
# ====================================================================
def test_09_cancelled_booking_excluded_from_kpi():
    future_date = (get_current_time() + timedelta(days=1)).strftime("%Y-%m-%d")

    res = book_slot(
        slot_id=2,
        body=BookingCreateRequest(date=future_date, start_time="10:00", duration_minutes=60)
    )
    bk_id = res["booking"]["booking_id"]

    # Before cancel: KPI = 1
    assert get_slots()["summary"]["booked_reserved"] == 1

    # Cancel booking
    delete_booking(bk_id)

    # After cancel: KPI = 0
    assert get_slots()["summary"]["booked_reserved"] == 0


# ====================================================================
# TEST 10: Booking immediately updates frontend counters
# ====================================================================
def test_10_booking_immediately_updates_frontend_counters():
    future_date = (get_current_time() + timedelta(days=2)).strftime("%Y-%m-%d")

    res = book_slot(
        slot_id=3,
        body=BookingCreateRequest(date=future_date, start_time="12:00", duration_minutes=60)
    )
    assert res["summary"]["booked_reserved"] == 1
    assert res["booking"]["booking_id"].startswith("BK-")


# ====================================================================
# TEST 11: Cancellation immediately updates frontend counters
# ====================================================================
def test_11_cancellation_immediately_updates_frontend_counters():
    future_date = (get_current_time() + timedelta(days=2)).strftime("%Y-%m-%d")

    res = book_slot(
        slot_id=3,
        body=BookingCreateRequest(date=future_date, start_time="12:00", duration_minutes=60)
    )
    bk_id = res["booking"]["booking_id"]

    del_res = delete_booking(bk_id)
    assert del_res["summary"]["booked_reserved"] == 0
    assert del_res["booking"]["booking_status"] == "CANCELLED"


# ====================================================================
# TEST 12: Multiple sequential bookings for the same slot
# ====================================================================
def test_12_multiple_sequential_bookings_same_slot():
    future_date = (get_current_time() + timedelta(days=3)).strftime("%Y-%m-%d")

    b1 = book_slot(slot_id=1, body=BookingCreateRequest(date=future_date, start_time="09:00", duration_minutes=30))
    b2 = book_slot(slot_id=1, body=BookingCreateRequest(date=future_date, start_time="09:31", duration_minutes=29))

    assert b1["booking"]["booking_id"].startswith("BK-")
    assert b2["booking"]["booking_id"].startswith("BK-")
    assert get_slots()["summary"]["booked_reserved"] == 2


# ====================================================================
# TEST 13: Fourth sequential booking
# ====================================================================
def test_13_fourth_sequential_booking():
    future_date = (get_current_time() + timedelta(days=4)).strftime("%Y-%m-%d")

    b1 = book_slot(slot_id=1, body=BookingCreateRequest(date=future_date, start_time="09:00", duration_minutes=30))
    b2 = book_slot(slot_id=1, body=BookingCreateRequest(date=future_date, start_time="09:31", duration_minutes=29))
    b3 = book_slot(slot_id=1, body=BookingCreateRequest(date=future_date, start_time="10:01", duration_minutes=29))
    b4 = book_slot(slot_id=1, body=BookingCreateRequest(date=future_date, start_time="10:31", duration_minutes=29))

    assert b1["booking"]["booking_id"].startswith("BK-")
    assert b2["booking"]["booking_id"].startswith("BK-")
    assert b3["booking"]["booking_id"].startswith("BK-")
    assert b4["booking"]["booking_id"].startswith("BK-")
    assert get_slots()["summary"]["booked_reserved"] == 4


# ====================================================================
# TEST 14: Same-slot overlapping booking rejected
# ====================================================================
def test_14_same_slot_overlapping_booking_rejected():
    future_date = (get_current_time() + timedelta(days=5)).strftime("%Y-%m-%d")

    book_slot(slot_id=1, body=BookingCreateRequest(date=future_date, start_time="14:00", duration_minutes=60))

    with pytest.raises(HTTPException) as exc:
        book_slot(slot_id=1, body=BookingCreateRequest(date=future_date, start_time="14:30", duration_minutes=30))
    assert exc.value.status_code == 409


# ====================================================================
# TEST 15: One-minute buffer enforced
# ====================================================================
def test_15_one_minute_buffer_enforced():
    future_date = (get_current_time() + timedelta(days=6)).strftime("%Y-%m-%d")

    book_slot(slot_id=1, body=BookingCreateRequest(date=future_date, start_time="15:00", duration_minutes=60))

    # Exactly at 16:00 (violating 1-min buffer after 16:00 end) -> REJECTED
    with pytest.raises(HTTPException) as exc:
        book_slot(slot_id=1, body=BookingCreateRequest(date=future_date, start_time="16:00", duration_minutes=30))
    assert exc.value.status_code == 409

    # At 16:01 (buffer satisfied) -> ACCEPTED
    b_ok = book_slot(slot_id=1, body=BookingCreateRequest(date=future_date, start_time="16:01", duration_minutes=30))
    assert b_ok["booking"]["booking_id"].startswith("BK-")


# ====================================================================
# TEST 16: Midnight-crossing booking
# ====================================================================
def test_16_midnight_crossing_booking():
    future_date = (get_current_time() + timedelta(days=7)).strftime("%Y-%m-%d")

    res = book_slot(slot_id=2, body=BookingCreateRequest(date=future_date, start_time="23:30", duration_minutes=60))
    assert res["booking"]["booking_id"].startswith("BK-")
    assert res["booking"]["start_time"] == "23:30"
    assert res["booking"]["end_time"] == "00:30"


# ====================================================================
# TEST 17: Page refresh preserves correct booking information
# ====================================================================
def test_17_page_refresh_preserves_correct_booking_information():
    future_date = (get_current_time() + timedelta(days=8)).strftime("%Y-%m-%d")
    book_slot(slot_id=1, body=BookingCreateRequest(date=future_date, start_time="10:00", duration_minutes=60))

    for _ in range(5):
        data = get_slots()
        summary = data["summary"]
        assert summary["total_slots"] == 3
        assert summary["physical_available"] == 3
        assert summary["booked_reserved"] == 1
        assert summary["available_now"] == 3


# ====================================================================
# TEST 18 & 19: Sensor payload with occupied=true / false
# ====================================================================
def test_18_19_sensor_payload_boolean():
    res_occ = update_sensor_status(SensorUpdateRequest(slot_id=1, occupied=True, distance_cm=8.0), _authorized=True)
    assert res_occ["physical_status"] == "Occupied"

    res_emp = update_sensor_status(SensorUpdateRequest(slot_id=1, occupied=False, distance_cm=170.0), _authorized=True)
    assert res_emp["physical_status"] == "Empty"


# ====================================================================
# TEST 20 & 21: Sensor payload with status="Occupied" / "Empty"
# ====================================================================
def test_20_21_sensor_payload_string():
    res_occ = update_sensor_status(SensorUpdateRequest(slot_id=2, status="Occupied", distance_cm=7.5), _authorized=True)
    assert res_occ["physical_status"] == "Occupied"

    res_emp = update_sensor_status(SensorUpdateRequest(slot_id=2, status="Empty", distance_cm=190.0), _authorized=True)
    assert res_emp["physical_status"] == "Empty"


# ====================================================================
# TEST 22: Sensor disconnection / error handling
# ====================================================================
def test_22_sensor_error_handling():
    res_err = update_sensor_status(SensorUpdateRequest(slot_id=3, status="Error", distance_cm=None), _authorized=True)
    assert res_err["physical_status"] == "Error"

    data = get_slots()
    assert data["summary"]["sensor_errors"] == 1
    assert data["slots"][2]["status"] == "Sensor Error"


# ====================================================================
# TEST 23: Booking table and dashboard KPI agree
# ====================================================================
def test_23_booking_table_and_dashboard_kpi_agree():
    future_date = (get_current_time() + timedelta(days=9)).strftime("%Y-%m-%d")
    book_slot(slot_id=1, body=BookingCreateRequest(date=future_date, start_time="10:00", duration_minutes=30))
    book_slot(slot_id=2, body=BookingCreateRequest(date=future_date, start_time="11:00", duration_minutes=30))

    bookings = list_bookings()["bookings"]
    active_future_bookings = [
        b for b in bookings
        if b["booking_status"] == "ACTIVE"
    ]
    summary = get_slots()["summary"]
    assert len(active_future_bookings) == summary["booked_reserved"] == 2


# ====================================================================
# TEST 24: Future booking does not incorrectly block current available time window
# ====================================================================
def test_24_future_booking_does_not_block_current_available_time_window():
    now = get_current_time()
    future_start = now + timedelta(hours=3)
    date_str = future_start.strftime("%Y-%m-%d")
    time_str = future_start.strftime("%H:%M")

    book_slot(slot_id=1, body=BookingCreateRequest(date=date_str, start_time=time_str, duration_minutes=60))

    data = get_slots()
    summary = data["summary"]
    slot1 = data["slots"][0]

    assert summary["available_now"] == 3
    assert slot1["is_available_now"] is True
    assert "Available now until" in slot1["availability_window"]
