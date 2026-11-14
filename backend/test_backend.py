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
# TEST A: All sensors Empty
# Expected: Total Slots = 3, Physical Occupied = 0, Physical Available = 3
# ====================================================================
def test_scenario_a_all_sensors_empty():
    for slot_id in (1, 2, 3):
        update_sensor_status(SensorUpdateRequest(slot_id=slot_id, status="Empty", distance_cm=180.0), _authorized=True)

    data = get_slots()
    summary = data["summary"]
    assert summary["total_slots"] == 3
    assert summary["physically_occupied"] == 0
    assert summary["physical_available"] == 3
    assert summary["available_now"] == 3
    assert summary["booked_reserved"] == 0


# ====================================================================
# TEST B: Slot 1 detects a car below 10 cm
# Expected: Red LED 1 ON, Green LED 1 OFF, Physical Occupied = 1, Physical Available = 2
# ====================================================================
def test_scenario_b_slot1_detects_car_below_10cm():
    update_sensor_status(SensorUpdateRequest(slot_id=1, status="Occupied", distance_cm=8.5), _authorized=True)

    data = get_slots()
    summary = data["summary"]
    assert summary["physically_occupied"] == 1
    assert summary["physical_available"] == 2
    assert summary["available_now"] == 2
    assert data["slots"][0]["status"] == "Occupied"
    assert data["slots"][0]["is_physically_occupied"] is True


# ====================================================================
# TEST C: Slots 1 and 2 detect cars
# Expected: Physical Occupied = 2, Physical Available = 1
# ====================================================================
def test_scenario_c_slots_1_and_2_detect_cars():
    update_sensor_status(SensorUpdateRequest(slot_id=1, status="Occupied", distance_cm=8.0), _authorized=True)
    update_sensor_status(SensorUpdateRequest(slot_id=2, status="Occupied", distance_cm=9.2), _authorized=True)

    data = get_slots()
    summary = data["summary"]
    assert summary["physically_occupied"] == 2
    assert summary["physical_available"] == 1
    assert summary["available_now"] == 1


# ====================================================================
# TEST D: All 3 sensors detect cars
# Expected: Physical Occupied = 3, Physical Available = 0
# ====================================================================
def test_scenario_d_all_three_sensors_detect_cars():
    for slot_id in (1, 2, 3):
        update_sensor_status(SensorUpdateRequest(slot_id=slot_id, status="Occupied", distance_cm=7.0), _authorized=True)

    data = get_slots()
    summary = data["summary"]
    assert summary["physically_occupied"] == 3
    assert summary["physical_available"] == 0
    assert summary["available_now"] == 0


# ====================================================================
# TEST E: Remove all cars
# Expected: Physical Occupied = 0, Physical Available = 3
# ====================================================================
def test_scenario_e_remove_all_cars():
    # Place cars first
    for slot_id in (1, 2, 3):
        update_sensor_status(SensorUpdateRequest(slot_id=slot_id, status="Occupied", distance_cm=7.0), _authorized=True)

    # Remove all cars (distance >= 10 cm)
    for slot_id in (1, 2, 3):
        update_sensor_status(SensorUpdateRequest(slot_id=slot_id, status="Empty", distance_cm=180.0), _authorized=True)

    data = get_slots()
    summary = data["summary"]
    assert summary["physically_occupied"] == 0
    assert summary["physical_available"] == 3
    assert summary["available_now"] == 3


# ====================================================================
# TEST F: Slot 1 is reserved but physically Empty
# Expected: Physical Occupied = 0, Physical Available = 3, Reserved = 1
# ====================================================================
def test_scenario_f_slot1_reserved_but_physically_empty():
    now = get_current_time()
    start_dt = now - timedelta(minutes=5)
    end_dt = now + timedelta(minutes=55)

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
            "BK-TESTF01",
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

    # Sensor reports Empty
    update_sensor_status(SensorUpdateRequest(slot_id=1, status="Empty", distance_cm=200.0), _authorized=True)

    data = get_slots()
    summary = data["summary"]
    assert summary["total_slots"] == 3
    assert summary["physically_occupied"] == 0
    assert summary["physical_available"] == 3, "Reserved but physically empty slot MUST be counted as physically available!"
    assert summary["booked_reserved"] == 1
    assert summary["available_now"] == 2, "Booking availability for immediate reservation is 2"
    assert data["slots"][0]["status"] == "Reserved"
    assert data["slots"][0]["is_currently_reserved"] is True
    assert data["slots"][0]["is_physically_occupied"] is False


# ====================================================================
# TEST G: Refresh simulation
# Expected: Available does not incorrectly change from 3 to 0.
# ====================================================================
def test_scenario_g_refresh_persistence():
    # Repeatedly query slots
    for _ in range(5):
        data = get_slots()
        summary = data["summary"]
        assert summary["total_slots"] == 3
        assert summary["physical_available"] == 3
        assert summary["physically_occupied"] == 0
        assert summary["available_now"] == 3


# ====================================================================
# TEST H: Create four sequential valid bookings for the same slot
# Booking 1: 09:00 to 09:30
# Booking 2: 09:31 to 10:00
# Booking 3: 10:01 to 10:30
# Booking 4: 10:31 to 11:00
# All should be accepted. Overlapping bookings must be rejected.
# ====================================================================
def test_scenario_h_four_sequential_bookings_same_slot():
    future_date = (get_current_time() + timedelta(days=2)).strftime("%Y-%m-%d")

    # Booking 1: 09:00 to 09:30
    b1 = book_slot(
        slot_id=1,
        body=BookingCreateRequest(date=future_date, start_time="09:00", duration_minutes=30)
    )
    assert b1["booking"]["booking_id"].startswith("BK-")

    # Booking 2: 09:31 to 10:00 (1-minute buffer after 09:30 respected)
    b2 = book_slot(
        slot_id=1,
        body=BookingCreateRequest(date=future_date, start_time="09:31", duration_minutes=29)
    )
    assert b2["booking"]["booking_id"].startswith("BK-")

    # Booking 3: 10:01 to 10:30 (1-minute buffer after 10:00 respected)
    b3 = book_slot(
        slot_id=1,
        body=BookingCreateRequest(date=future_date, start_time="10:01", duration_minutes=29)
    )
    assert b3["booking"]["booking_id"].startswith("BK-")

    # Booking 4: 10:31 to 11:00 (The fourth sequential booking!)
    b4 = book_slot(
        slot_id=1,
        body=BookingCreateRequest(date=future_date, start_time="10:31", duration_minutes=29)
    )
    assert b4["booking"]["booking_id"].startswith("BK-")

    # Verify all 4 bookings exist in database
    b_list = list_bookings(slot_id=1)["bookings"]
    assert len(b_list) == 4

    # Overlapping booking attempt: 09:15 to 09:45 -> MUST BE REJECTED
    with pytest.raises(HTTPException) as exc1:
        book_slot(
            slot_id=1,
            body=BookingCreateRequest(date=future_date, start_time="09:15", duration_minutes=30)
        )
    assert exc1.value.status_code == 409

    # Buffer violation attempt: 09:30 to 10:00 (starts right at 09:30, violating buffer) -> REJECTED
    with pytest.raises(HTTPException) as exc2:
        book_slot(
            slot_id=1,
            body=BookingCreateRequest(date=future_date, start_time="09:30", duration_minutes=30)
        )
    assert exc2.value.status_code == 409


# ====================================================================
# TEST I: Cancel an active booking
# Expected: Removed from active bookings, historical record in MySQL retained, slot interval released
# ====================================================================
def test_scenario_i_cancellation_preserves_history():
    future_date = (get_current_time() + timedelta(days=3)).strftime("%Y-%m-%d")

    b = book_slot(
        slot_id=2,
        body=BookingCreateRequest(date=future_date, start_time="14:00", duration_minutes=60)
    )
    bk_id = b["booking"]["booking_id"]

    # Cancel the booking
    cancel_res = delete_booking(bk_id)
    assert cancel_res["booking"]["booking_status"] == "CANCELLED"

    # Verify record still exists in database with status CANCELLED
    record = get_booking(bk_id)
    assert record["booking_id"] == bk_id
    assert record["booking_status"] == "CANCELLED"

    # Verify interval is free again
    rebook = book_slot(
        slot_id=2,
        body=BookingCreateRequest(date=future_date, start_time="14:00", duration_minutes=60)
    )
    assert rebook["booking"]["booking_id"].startswith("BK-")


# ====================================================================
# TEST J & K: Sensor payload compatibility (Boolean and String)
# ====================================================================
def test_sensor_payload_boolean_compatibility():
    # Test {"slot_id": 1, "occupied": true}
    res_occ = update_sensor_status(
        SensorUpdateRequest(slot_id=1, occupied=True, distance_cm=7.5),
        _authorized=True
    )
    assert res_occ["physical_status"] == "Occupied"

    # Test {"slot_id": 1, "occupied": false}
    res_emp = update_sensor_status(
        SensorUpdateRequest(slot_id=1, occupied=False, distance_cm=160.0),
        _authorized=True
    )
    assert res_emp["physical_status"] == "Empty"


# ====================================================================
# Direct POST /bookings endpoint
# ====================================================================
def test_direct_post_bookings_endpoint():
    future_date = (get_current_time() + timedelta(days=4)).strftime("%Y-%m-%d")

    res = create_booking_direct(
        body=BookingCreateRequestWithSlot(
            slot_id=3,
            date=future_date,
            start_time="15:00",
            duration_minutes=45
        )
    )
    assert res["booking"]["booking_id"].startswith("BK-")
    assert res["booking"]["slot_id"] == 3


# ====================================================================
# Midnight crossing booking test
# ====================================================================
def test_midnight_crossing_booking():
    future_date = (get_current_time() + timedelta(days=5)).strftime("%Y-%m-%d")

    res = book_slot(
        slot_id=1,
        body=BookingCreateRequest(
            date=future_date,
            start_time="23:30",
            duration_minutes=60,
        )
    )
    assert res["booking"]["booking_id"].startswith("BK-")
    assert res["booking"]["start_time"] == "23:30"
    assert res["booking"]["end_time"] == "00:30"
