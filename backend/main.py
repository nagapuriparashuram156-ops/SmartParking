
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta

app = FastAPI(title="Smart Parking API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://smartparking-39mz.onrender.com",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

bookings = {i: [] for i in range(1, 4)}


@app.get("/")
def home():
    return {"message": "Smart Parking API is running"}


@app.get("/slots")
def get_slots():
    now = datetime.now()

    for slot_id in slots:
        active = [
            b for b in bookings[slot_id]
            if datetime.fromisoformat(b["end"]) > now
        ]

        if active:
            latest = max(active, key=lambda b: b["start"])
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

    return {"slots": list(slots.values())}


@app.post("/book/{slot_id}")
def book_slot(
    slot_id: int,
    date: str,
    start_time: str,
    duration_minutes: int,
):
    if slot_id not in slots:
        raise HTTPException(status_code=404, detail="Invalid slot")

    if duration_minutes < 1 or duration_minutes > 1440:
        raise HTTPException(status_code=400, detail="Invalid duration")

    try:
        start = datetime.strptime(
            f"{date} {start_time}", "%Y-%m-%d %H:%M"
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date or time")

    end = start + timedelta(minutes=duration_minutes)

    if start < datetime.now():
        raise HTTPException(status_code=400, detail="Choose a future time")

    for booking in bookings[slot_id]:
        existing_start = datetime.fromisoformat(booking["start"])
        existing_end = datetime.fromisoformat(booking["end"])

        # One-minute buffer between bookings
        if start < existing_end + timedelta(minutes=1) and \
           end + timedelta(minutes=1) > existing_start:
            raise HTTPException(
                status_code=409,
                detail="This slot is already booked for that time"
            )

    new_booking = {
        "date": date,
        "start_time": start_time,
        "end_time": end.strftime("%H:%M"),
        "duration_minutes": duration_minutes,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }

    bookings[slot_id].append(new_booking)

    return {
        "message": "Booking confirmed",
        "slot": {
            "id": slot_id,
            "status": "Booked",
            **new_booking,
        },
    }


@app.post("/cancel/{slot_id}")
def cancel_booking(slot_id: int, date: str, time: str):
    if slot_id not in slots:
        raise HTTPException(status_code=404, detail="Invalid slot")

    for booking in bookings[slot_id]:
        if booking["date"] == date and booking["start_time"] == time:
            bookings[slot_id].remove(booking)
            return {"message": "Booking cancelled"}

    raise HTTPException(status_code=404, detail="Booking not found")


@app.get("/sensor/status")
def sensor_status():
    return {"slots": list(slots.values())}
