
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
from pydantic import BaseModel

app = FastAPI(title="Smart Parking API")

# Allow the deployed frontend and local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://smartparking-39mz.onrender.com",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Three parking slots
slots = {
    i: {
        "id": i,
        "status": "Available",
        "date": None,
        "start_time": None,
        "end_time": None,
        "duration_minutes": None,
    }
    for i in range(1, 4)
}

# Store bookings in memory
bookings = {1: [], 2: [], 3: []}


def parse_booking(date_str, time_str, duration):
    try:
        start = datetime.strptime(
            f"{date_str} {time_str}", "%Y-%m-%d %H:%M"
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date or start time. Use YYYY-MM-DD and HH:MM."
        )

    if duration < 1 or duration > 1440:
        raise HTTPException(
            status_code=400,
            detail="Duration must be between 1 and 1440 minutes."
        )

    end = start + timedelta(minutes=duration)
    return start, end


@app.get("/")
def home():
    return {"message": "Smart Parking API is running"}


@app.get("/slots")
def get_slots():
    # Update the slot status based on its latest booking
    result = []

    for slot_id, slot in slots.items():
        active_bookings = bookings[slot_id]

        if active_bookings:
            latest = active_bookings[-1]
            slot.update({
                "status": "Booked",
                "date": latest["date"],
                "start_time": latest["start_time"],
                "end_time": latest["end_time"],
                "duration_minutes": latest["duration_minutes"],
            })
        else:
            slot.update({
                "status": "Available",
                "date": None,
                "start_time": None,
                "end_time": None,
                "duration_minutes": None,
            })

        result.append(slot.copy())

    return {"slots": result}


@app.post("/book/{slot_id}")
def book_slot(
    slot_id: int,
    date: str,
    start_time: str,
    duration_minutes: int = 60
):
    if slot_id not in slots:
        raise HTTPException(status_code=404, detail="Invalid parking slot.")

    start, end = parse_booking(date, start_time, duration_minutes)

    # One-minute buffer after each booking
    for booking in bookings[slot_id]:
        existing_start = datetime.strptime(
            f'{booking["date"]} {booking["start_time"]}',
            "%Y-%m-%d %H:%M"
        )
        existing_end = datetime.strptime(
            f'{booking["date"]} {booking["end_time"]}',
            "%Y-%m-%d %H:%M"
        )

        buffered_start = existing_start - timedelta(minutes=1)
        buffered_end = existing_end + timedelta(minutes=1)

        if start < buffered_end and end > buffered_start:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Slot {slot_id} is already booked during this time. "
                    f"Existing booking: {booking['date']} "
                    f"{booking['start_time']}–{booking['end_time']}. "
                    "Please select another time."
                )
            )

    booking = {
        "slot_id": slot_id,
        "date": date,
        "start_time": start.strftime("%H:%M"),
        "end_time": end.strftime("%H:%M"),
        "duration_minutes": duration_minutes,
    }

    bookings[slot_id].append(booking)

    slots[slot_id].update({
        "status": "Booked",
        "date": booking["date"],
        "start_time": booking["start_time"],
        "end_time": booking["end_time"],
        "duration_minutes": booking["duration_minutes"],
    })

    return {
        "message": "Booking confirmed successfully!",
        "slot": slots[slot_id],
        "booking": booking,
    }


@app.post("/cancel/{slot_id}")
def cancel_booking(
    slot_id: int,
    date: str,
    time: str
):
    if slot_id not in slots:
        raise HTTPException(status_code=404, detail="Invalid parking slot.")

    slot_bookings = bookings[slot_id]

    for index, booking in enumerate(slot_bookings):
        if booking["date"] == date and booking["start_time"] == time:
            slot_bookings.pop(index)

            if slot_bookings:
                latest = slot_bookings[-1]
                slots[slot_id].update({
                    "status": "Booked",
                    "date": latest["date"],
                    "start_time": latest["start_time"],
                    "end_time": latest["end_time"],
                    "duration_minutes": latest["duration_minutes"],
                })
            else:
                slots[slot_id].update({
                    "status": "Available",
                    "date": None,
                    "start_time": None,
                    "end_time": None,
                    "duration_minutes": None,
                })

            return {
                "message": f"Booking for Slot {slot_id} cancelled successfully.",
                "slot": slots[slot_id],
            }

    raise HTTPException(
        status_code=404,
        detail="Matching booking was not found."
    )


# Sensor API
class SensorUpdate(BaseModel):
    slot_id: int
    status: str


@app.get("/sensor/status")
def get_sensor_status():
    return {"slots": list(slots.values())}


@app.post("/sensor/update")
def update_sensor_status(data: SensorUpdate):
    if data.slot_id not in slots:
        raise HTTPException(status_code=404, detail="Invalid slot ID.")

    value = data.status.strip().lower()

    if value in ("occupied", "full", "vehicle", "vehicle_detected"):
        slots[data.slot_id]["status"] = "Occupied"
    elif value in ("empty", "available", "vacant", "no_vehicle"):
        slots[data.slot_id]["status"] = "Available"
    else:
        raise HTTPException(
            status_code=400,
            detail="Status must be Empty/Available or Occupied."
        )

    return {
        "message": "Sensor status updated.",
        "slot": slots[data.slot_id],
    }
