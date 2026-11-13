
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
from pydantic import BaseModel

app = FastAPI(title="Smart Parking API")

# CORS: allow deployed Render frontend and local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://smartparking-39mz.onrender.com",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_origin_regex=r"https://.*\.onrender\.com",
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

# In-memory booking storage
bookings = {1: [], 2: [], 3: []}

# Separate sensor states from booking states
sensor_states = {1: "Empty", 2: "Empty", 3: "Empty"}

BUFFER_MINUTES = 1


def parse_booking(date_str, time_str, duration):
    try:
        start = datetime.strptime(
            f"{date_str} {time_str}",
            "%Y-%m-%d %H:%M"
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date or time. Use YYYY-MM-DD and HH:MM."
        )

    if duration < 1 or duration > 1440:
        raise HTTPException(
            status_code=400,
            detail="Duration must be between 1 and 1440 minutes."
        )

    end = start + timedelta(minutes=duration)
    return start, end


def booking_to_response(booking):
    return {
        "slot_id": booking["slot_id"],
        "date": booking["date"],
        "start_time": booking["start"].strftime("%H:%M"),
        "end_time": booking["end"].strftime("%H:%M"),
        "duration_minutes": booking["duration_minutes"],
    }


def get_slot_data(slot_id):
    slot_bookings = bookings[slot_id]

    # Show the latest booking details if bookings exist
    latest = slot_bookings[-1] if slot_bookings else None

    # Sensor status takes priority for physical occupancy
    if sensor_states[slot_id] == "Occupied":
        status = "Occupied"
    else:
        # Booking availability is time-based, not permanently Booked
        status = "Available"

    return {
        "id": slot_id,
        "status": status,
        "date": latest["date"] if latest else None,
        "start_time": latest["start"].strftime("%H:%M") if latest else None,
        "end_time": latest["end"].strftime("%H:%M") if latest else None,
        "duration_minutes": latest["duration_minutes"] if latest else None,
    }


@app.get("/")
def home():
    return {"message": "Smart Parking API is running"}


@app.get("/slots")
def get_slots():
    return {"slots": [get_slot_data(i) for i in range(1, 4)]}


@app.post("/book/{slot_id}")
def book_slot(
    slot_id: int,
    date: str,
    start_time: str,
    duration_minutes: int = 60
):
    if slot_id not in slots:
        raise HTTPException(status_code=404, detail="Invalid parking slot.")

    if sensor_states[slot_id] == "Occupied":
        raise HTTPException(
            status_code=409,
            detail=f"Slot {slot_id} is currently occupied by a vehicle."
        )

    start, end = parse_booking(date, start_time, duration_minutes)

    # Check conflicts including one-minute buffer
    for existing in bookings[slot_id]:
        existing_start = existing["start"]
        existing_end = existing["end"]

        buffered_start = existing_start - timedelta(minutes=BUFFER_MINUTES)
        buffered_end = existing_end + timedelta(minutes=BUFFER_MINUTES)

        if start < buffered_end and end > buffered_start:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Slot {slot_id} is unavailable during this time. "
                    f"Existing booking: {existing['date']} "
                    f"{existing_start.strftime('%H:%M')}–"
                    f"{existing_end.strftime('%H:%M')}. "
                    "Choose another time."
                )
            )

    booking = {
        "slot_id": slot_id,
        "date": start.strftime("%Y-%m-%d"),
        "start": start,
        "end": end,
        "duration_minutes": duration_minutes,
    }

    bookings[slot_id].append(booking)

    return {
        "message": "Booking confirmed successfully!",
        "slot": get_slot_data(slot_id),
        "booking": booking_to_response(booking),
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
        if (
            booking["date"] == date
            and booking["start"].strftime("%H:%M") == time
        ):
            slot_bookings.pop(index)

            return {
                "message": f"Booking for Slot {slot_id} cancelled successfully.",
                "slot": get_slot_data(slot_id),
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
    return {
        "slots": [
            {
                "id": slot_id,
                "status": sensor_states[slot_id],
                "updated_at": None,
            }
            for slot_id in range(1, 4)
        ]
    }


@app.post("/sensor/update")
def update_sensor_status(data: SensorUpdate):
    if data.slot_id not in slots:
        raise HTTPException(status_code=404, detail="Invalid slot ID.")

    value = data.status.strip().lower()

    if value in ("occupied", "full", "vehicle", "vehicle_detected"):
        sensor_states[data.slot_id] = "Occupied"

    elif value in ("empty", "available", "vacant", "no_vehicle"):
        sensor_states[data.slot_id] = "Empty"

    else:
        raise HTTPException(
            status_code=400,
            detail="Status must be Empty/Available or Occupied."
        )

    return {
        "message": "Sensor status updated.",
        "slot": get_slot_data(data.slot_id),
    }
