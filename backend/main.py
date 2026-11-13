
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime

app = FastAPI(title="Smart Parking API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://smartparking-39mz.onrender.com"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

slots = {
    i: {"id": i, "status": "Empty", "updated_at": None}
    for i in range(1, 4)
}

class SensorUpdate(BaseModel):
    slot_id: int
    status: str

@app.get("/")
def home():
    return {"message": "Smart Parking API is running"}

@app.get("/sensor/status")
def get_status():
    return {"slots": list(slots.values())}

@app.post("/sensor/update")
def update_status(data: SensorUpdate):
    if data.slot_id not in slots:
        raise HTTPException(
            status_code=404,
            detail="slot_id must be 1, 2, or 3"
        )

    value = data.status.strip().lower()

    if value in ("occupied", "full", "vehicle", "vehicle_detected"):
        status = "Occupied"
    elif value in ("empty", "available", "vacant", "no_vehicle"):
        status = "Empty"
    else:
        raise HTTPException(
            status_code=400,
            detail="status must be Empty or Occupied"
        )

    slots[data.slot_id]["status"] = status
    slots[data.slot_id]["updated_at"] = datetime.now().isoformat(
        timespec="seconds"
    )

    return {"message": "Updated", "slot": slots[data.slot_id]}
