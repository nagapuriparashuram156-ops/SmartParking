
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

app = FastAPI(title="Smart Parking API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://smartparking-39mz.onrender.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Three parking slots
slots = {
    i: {
        "id": i,
        "status": "Available",
        "updated_at": None,
    }
    for i in range(1, 4)
}

# Temporary booking storage
bookings = {}


class SensorUpdate(BaseModel):
    slot_id: int
    status: str


class BookingRequest(BaseModel):
    slot_id: int
    date: str                 # YYYY-MM-DD
    start_time: str           # HH:MM
    duration_minutes: int
    user_name: Optional[str] = "Guest"
    vehicle_number: Optional[str] = ""


def get_booking_times(data):
    try:
        start = datetime.strptime(
            f"{data.date} {data.start_time}",
            "%Y-%m-%d %H:%M"
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Date must be YYYY-MM-DD and time must be HH:MM"
        )

    if data.duration_minutes <= 0:
        raise HTTPException(
            status_code=400,
            detail="Duration must be greater than zero"
        )

    end = start + timedelta(minutes=data.duration_minutes)
    return start, end


def check_overlap(slot_id, start, end):
    for booking in bookings.values():
        if booking["slot_id"] != slot_id:
            continue

        existing_start = datetime.fromisoformat(booking["start"])
        existing_end = datetime.fromisoformat(booking["end"])

        # One-minute buffer after existing booking
        buffered_end = existing_end + timedelta(minutes=1)

        if start < buffered_end and end > existing_start:
            return True

    return False


@app.get("/")
def home():
    return {"message": "Smart Parking API is running"}


@app.get("/slots")
def get_slots():
    return {
        "slots": list(slots.values()),
        "bookings": list(bookings.values())
    }


@app.get("/sensor/status")
def get_sensor_status():
    return {"slots": list(slots.values())}


@app.post("/sensor/update")
def update_sensor_status(data: SensorUpdate):
    if data.slot_id not in slots:
        raise HTTPException(
            status_code=404,
            detail="Slot ID must be 1, 2, or 3"
        )

    value = data.status.strip().lower()

    if value in ("occupied", "full", "vehicle", "vehicle_detected"):
        status = "Occupied"
    elif value in ("empty", "available", "vacant", "no_vehicle"):
        status = "Available"
    else:
        raise HTTPException(
            status_code=400,
            detail="Status must be Empty or Occupied"
        )

    slots[data.slot_id]["status"] = status
    slots[data.slot_id]["updated_at"] = datetime.now().isoformat(
        timespec="seconds"
    )

    return {"message": "Sensor updated", "slot": slots[data.slot_id]}


@app.get("/availability/{slot_id}")
def check_availability(
    slot_id: int,
    date: str,
    start_time: str,
    duration_minutes: int
):
    if slot_id not in slots:
        raise HTTPException(status_code=404, detail="Slot not found")

    request = BookingRequest(
        slot_id=slot_id,
        date=date,
        start_time=start_time,
        duration_minutes=duration_minutes
    )

    start, end = get_booking_times(request)

    if slots[slot_id]["status"] == "Occupied":
        return {"available": False, "message": "Slot is occupied"}

    if check_overlap(slot_id, start, end):
        return {
            "available": False,
            "message": "Time overlaps with another booking"
        }

    return {"available": True, "message": "Slot is available"}


@app.post("/book")
def book_parking(data: BookingRequest):
    if data.slot_id not in slots:
        raise HTTPException(status_code=404, detail="Slot not found")

    start, end = get_booking_times(data)

    if start <= datetime.now():
        raise HTTPException(
            status_code=400,
            detail="Booking must be for a future time"
        )

    if slots[data.slot_id]["status"] == "Occupied":
        raise HTTPException(
            status_code=409,
            detail="This parking slot is currently occupied"
        )

    if check_overlap(data.slot_id, start, end):
        raise HTTPException(
            status_code=409,
            detail="This time is already booked. Choose another time."
        )

    booking_id = str(uuid4())[:8]

    booking = {
        "booking_id": booking_id,
        "slot_id": data.slot_id,
        "user_name": data.user_name,
        "vehicle_number": data.vehicle_number,
        "date": data.date,
        "start_time": data.start_time,
        "duration_minutes": data.duration_minutes,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "status": "Booked"
    }

    bookings[booking_id] = booking

    return {
        "message": "Parking booked successfully",
        "booking": booking
    }


@app.get("/bookings")
def get_bookings():
    return {"bookings": list(bookings.values())}


@app.delete("/cancel/{booking_id}")
def cancel_booking(booking_id: str):
    if booking_id not in bookings:
        raise HTTPException(status_code=404, detail="Booking not found")

    deleted_booking = bookings.pop(booking_id)

    return {
        "message": "Booking cancelled successfully",
        "booking": deleted_booking
    }
