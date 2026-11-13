from datetime import datetime, timedelta
from threading import RLock

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Smart Parking API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://smartparking-39mz.onrender.com",
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:5174", "http://127.0.0.1:5174",
        "http://localhost:5175", "http://127.0.0.1:5175",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LOCK = RLock()
BUFFER_MINUTES = 1
SLOT_IDS = (1, 2, 3)
sensor_state = {sid: "Empty" for sid in SLOT_IDS}
bookings = {sid: [] for sid in SLOT_IDS}


class SensorUpdate(BaseModel):
    slot_id: int
    status: str


def parse_datetime(date_text: str, time_text: str) -> datetime:
    try:
        return datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M")
    except ValueError:
        raise HTTPException(status_code=400, detail="Date format YYYY-MM-DD and time HH:MM required.")


def active(booking):
    return not booking["cancelled"]


def make_slot(sid):
    now = datetime.now()
    items = sorted((b for b in bookings[sid] if active(b)), key=lambda b: b["start"])
    upcoming = next((b for b in items if b["end"] >= now), None)
    if sensor_state[sid] == "Occupied":
        status = "Occupied"
    elif upcoming:
        status = "Booked"
    else:
        status = "Available"
    result = {"id": sid, "status": status, "sensor_status": sensor_state[sid]}
    if items:
        latest = items[-1]
        result.update(date=latest["date"], start_time=latest["start_time"], end_time=latest["end_time"])
    return result


@app.get("/")
def home():
    return {"message": "Smart Parking API is running", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/slots")
def get_slots():
    with LOCK:
        return {"slots": [make_slot(sid) for sid in SLOT_IDS]}


@app.get("/sensor/status")
def get_sensor_status():
    with LOCK:
        return {"slots": [{"id": sid, "status": sensor_state[sid], "updated_at": None} for sid in SLOT_IDS]}


@app.post("/sensor/update")
def update_sensor(data: SensorUpdate):
    if data.slot_id not in sensor_state:
        raise HTTPException(status_code=404, detail="slot_id must be 1, 2, or 3")
    val = data.status.strip().lower()
    if val in ("occupied", "full", "vehicle", "vehicle_detected"):
        normalized = "Occupied"
    elif val in ("empty", "available", "vacant", "no_vehicle"):
        normalized = "Empty"
    else:
        raise HTTPException(status_code=400, detail="status must be Empty or Occupied")
    with LOCK: