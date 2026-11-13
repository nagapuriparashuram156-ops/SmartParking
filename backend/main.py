import os
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Depends, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from database import db, get_current_time

from contextlib import asynccontextmanager

# Lifespan: Initialize Database Tables and Seeds
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db.init_db()
        print("[DATABASE] Initialized successfully.")
    except Exception as e:
        print(f"[DATABASE ERROR] Initialization failed: {e}")
    yield

app = FastAPI(
    title="Smart Parking System API",
    description="Full-stack IoT Smart Parking backend with ESP32 integration, MySQL persistence, and real-time dashboard state.",
    version="2.0.0",
    lifespan=lifespan,
)

# Configurable Sensor API Token for ESP32 authentication
SENSOR_API_TOKEN = os.getenv("SENSOR_API_TOKEN", "smartpark_esp32_secret_2026")

# CORS Configuration
frontend_env = os.getenv("FRONTEND_ORIGINS", "")
allowed_origins = [
    "https://smartparking-mobile.onrender.com",
    "https://smartparking-39mz.onrender.com",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
if frontend_env:
    for origin in frontend_env.split(","):
        clean = origin.strip()
        if clean and clean not in allowed_origins:
            allowed_origins.append(clean)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=r"^https://.*\.onrender\.com$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pydantic Schemas
class SensorUpdateRequest(BaseModel):
    slot_id: int = Field(..., ge=1, le=3, description="Parking slot ID (1, 2, or 3)")
    status: str = Field(..., description="Sensor physical state: 'Occupied', 'Empty', or 'Error'")
    distance_cm: Optional[float] = Field(None, description="Ultrasonic distance in centimeters")


class BookingCreateRequest(BaseModel):
    date: Optional[str] = Field(None, description="Booking date YYYY-MM-DD")
    start_time: Optional[str] = Field(None, description="Start time HH:MM")
    duration_minutes: Optional[int] = Field(60, ge=1, le=1440, description="Duration in minutes (1 - 1440)")


class CancelSlotRequest(BaseModel):
    booking_id: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None



# Dependency: Verify Hardware Sensor Token
def verify_sensor_token(
    request: Request,
    x_sensor_token: Optional[str] = Header(None, alias="X-Sensor-Token"),
    token: Optional[str] = Query(None)
):
    auth_header = request.headers.get("Authorization", "")
    bearer_token = None
    if auth_header.startswith("Bearer "):
        bearer_token = auth_header.split("Bearer ")[1].strip()

    provided_token = x_sensor_token or bearer_token or token

    # Check secret token
    if not provided_token or provided_token != SENSOR_API_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Invalid or missing sensor authentication token."
        )
    return True


# ====================================================================
# API Endpoints
# ====================================================================

@app.get("/")
def home():
    now = get_current_time()
    return {
        "system": "Smart Parking System",
        "status": "Online",
        "version": "2.0.0",
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Kolkata",
        "endpoints": {
            "health": "/health",
            "slots": "/slots",
            "sensor_status": "/sensor/status",
            "sensor_update": "POST /sensor/update",
            "bookings": "/bookings",
            "book_slot": "POST /book/{slot_id}",
            "cancel_booking": "DELETE /bookings/{booking_id}",
            "cancel_slot": "POST /cancel/{slot_id}",
        }
    }


@app.get("/health")
def health_check():
    """System health check endpoint for monitoring, Docker, and Render."""
    now = get_current_time()
    db_ok = False
    db_type = "unknown"
    error_msg = None

    try:
        conn, db_type = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1;")
        cursor.fetchone()
        cursor.close()
        conn.close()
        db_ok = True
    except Exception as e:
        error_msg = str(e)

    status_code = 200 if db_ok else 503
    response_payload = {
        "status": "healthy" if db_ok else "unhealthy",
        "database": {
            "connected": db_ok,
            "engine": db_type,
            "error": error_msg,
        },
        "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Kolkata",
    }
    if not db_ok:
        raise HTTPException(status_code=status_code, detail=response_payload)
    return response_payload


@app.get("/slots")
def get_slots():
    """
    Returns real-time slot states, physical sensor data, active bookings,
    and accurate dashboard counts.
    """
    try:
        return db.get_slots_with_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error fetching slots: {str(e)}")


@app.get("/slots/{slot_id}/intervals")
def get_slot_intervals(slot_id: int, date: Optional[str] = Query(None)):
    """Returns booked intervals for a slot on a given date to facilitate time window selection."""
    if slot_id not in (1, 2, 3):
        raise HTTPException(status_code=404, detail="Invalid slot ID.")
    try:
        bookings = db.get_bookings(slot_id=slot_id, status="ACTIVE")
        if date:
            bookings = [b for b in bookings if b.get("booking_date") == date]
        intervals = [
            {
                "booking_id": b["booking_id"],
                "date": b["booking_date"],
                "start_time": str(b["start_time"])[:5],
                "end_time": str(b["end_time"])[:5],
                "duration_minutes": b["duration_minutes"],
            }
            for b in bookings
        ]
        return {
            "slot_id": slot_id,
            "date": date,
            "booked_intervals": intervals,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error fetching intervals: {str(e)}")


@app.get("/sensor/status")
def get_sensor_status():
    """Hardware sensor status for all 3 slots."""
    try:
        sensor_list = db.get_all_sensor_status()
        return {
            "slots": sensor_list,
            "server_time": get_current_time().strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": "Asia/Kolkata",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error fetching sensor status: {str(e)}")


@app.post("/sensor/update")
def update_sensor_status(
    data: SensorUpdateRequest,
    _authorized: bool = Depends(verify_sensor_token)
):
    """
    Receives physical ultrasonic sensor telemetry from ESP32.
    Requires X-Sensor-Token header.
    Normalizes statuses into 'Occupied', 'Empty', or 'Error'.
    """
    raw_status = data.status.strip().lower()

    if raw_status in ("occupied", "full", "vehicle", "vehicle_detected", "present", "1"):
        normalized_status = "Occupied"
    elif raw_status in ("empty", "available", "vacant", "no_vehicle", "clear", "0"):
        normalized_status = "Empty"
    elif raw_status in ("error", "sensor_error", "timeout", "unknown", "disconnected", "-1"):
        normalized_status = "Error"
    else:
        raise HTTPException(
            status_code=400,
            detail="Invalid status. Valid values: 'Occupied', 'Empty', 'Error'."
        )

    # Sanity check on distance readings
    dist = data.distance_cm
    if dist is not None:
        if dist < 0 or dist > 500:
            normalized_status = "Error"

    try:
        db.update_sensor_status(data.slot_id, normalized_status, dist)
        slot_info = db.get_slots_with_status()
        updated_slot = next((s for s in slot_info["slots"] if s["id"] == data.slot_id), None)
        return {
            "message": f"Sensor update recorded for Slot {data.slot_id}.",
            "slot_id": data.slot_id,
            "physical_status": normalized_status,
            "distance_cm": dist,
            "slot": updated_slot,
            "summary": slot_info["summary"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to record sensor update: {str(e)}")


@app.post("/book/{slot_id}")
def book_slot(
    slot_id: int,
    # Support both JSON body and URL query params
    body: Optional[BookingCreateRequest] = None,
    date: Optional[str] = Query(None),
    start_time: Optional[str] = Query(None),
    duration_minutes: Optional[int] = Query(None),
):
    """
    Reserves a parking slot with strict 1-minute buffer and conflict checks.
    Accepts date, start_time, and duration_minutes via query parameters or JSON body.
    """
    if slot_id not in (1, 2, 3):
        raise HTTPException(status_code=404, detail=f"Slot {slot_id} does not exist. Available slots: 1, 2, 3.")

    # Merge body and query params
    req_date = (body and body.date) or date
    req_start_time = (body and body.start_time) or start_time
    req_duration = (body and body.duration_minutes) or duration_minutes or 60

    if not req_date or not req_start_time:
        raise HTTPException(status_code=400, detail="Missing required booking parameters: 'date' (YYYY-MM-DD) and 'start_time' (HH:MM).")

    try:
        booking_result = db.create_booking(
            slot_id=slot_id,
            date_str=req_date,
            start_time_str=req_start_time,
            duration_minutes=req_duration,
        )

        slot_state = db.get_slots_with_status()
        current_slot = next((s for s in slot_state["slots"] if s["id"] == slot_id), None)

        return {
            "message": f"Booking confirmed successfully for Slot {slot_id}!",
            "booking": booking_result,
            "slot": current_slot,
            "summary": slot_state["summary"],
        }
    except ValueError as ve:
        raise HTTPException(status_code=409, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error processing booking: {str(e)}")


@app.get("/bookings")
def list_bookings(
    slot_id: Optional[int] = Query(None, ge=1, le=3),
    status: Optional[str] = Query(None),
):
    """Lists all reservations with optional filters."""
    try:
        bookings = db.get_bookings(slot_id=slot_id, status=status)
        return {
            "total_bookings": len(bookings),
            "bookings": bookings,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error fetching bookings: {str(e)}")


@app.get("/bookings/{booking_id}")
def get_booking(booking_id: str):
    """Fetches details of a specific reservation by booking ID."""
    try:
        booking = db.get_booking_by_id(booking_id)
        if not booking:
            raise HTTPException(status_code=404, detail=f"Booking '{booking_id}' not found.")
        return booking
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error fetching booking: {str(e)}")


@app.delete("/bookings/{booking_id}")
def delete_booking(booking_id: str):
    """Cancels a reservation using its unique booking ID."""
    try:
        result = db.cancel_booking(booking_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"Booking '{booking_id}' not found.")

        slot_state = db.get_slots_with_status()
        return {
            "message": f"Booking '{booking_id}' cancelled successfully.",
            "booking": result,
            "summary": slot_state["summary"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error cancelling booking: {str(e)}")


@app.post("/cancel/{slot_id}")
def cancel_slot(
    slot_id: int,
    body: Optional[CancelSlotRequest] = None,
    booking_id: Optional[str] = Query(None),
    date: Optional[str] = Query(None),
    time: Optional[str] = Query(None),
):
    """
    Cancels reservation on a slot by booking_id or date/time query parameters.
    Maintains backward compatibility with legacy frontend queries while supporting ID.
    """
    if slot_id not in (1, 2, 3):
        raise HTTPException(status_code=404, detail="Invalid parking slot.")

    target_booking_id = (body and body.booking_id) or booking_id
    target_date = (body and body.date) or date
    target_time = (body and body.time) or time

    try:
        result = db.cancel_slot_booking(
            slot_id=slot_id,
            date=target_date,
            time=target_time,
            booking_id=target_booking_id,
        )

        if not result:
            raise HTTPException(
                status_code=404,
                detail=f"No matching active reservation found for Slot {slot_id}."
            )

        slot_state = db.get_slots_with_status()
        current_slot = next((s for s in slot_state["slots"] if s["id"] == slot_id), None)

        return {
            "message": f"Booking for Slot {slot_id} cancelled successfully.",
            "booking": result,
            "slot": current_slot,
            "summary": slot_state["summary"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error cancelling booking: {str(e)}")
