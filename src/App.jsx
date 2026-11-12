import React from "react";
import { QRCodeSVG } from "qrcode.react";
import { useState, useEffect } from "react";
import "./style.css";

function App() {
  const [slots, setSlots] = useState([
    { id: 1, status: "Available" },
    { id: 2, status: "Available" },
    { id: 3, status: "Available" },
  ]);

  const [selectedSlot, setSelectedSlot] = useState(null);
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [duration, setDuration] = useState(60);
  const [message, setMessage] = useState("");
  const API_BASES = [`http://${window.location.hostname}:8001`, `http://${window.location.hostname}:8000`];

  async function apiFetch(path, options = {}) {
    let lastError;
    for (const base of API_BASES) {
      try {
        const response = await fetch(`${base}${path}`, options);
        // If this port responds but doesn't provide the booking route, try the other backend port.
        if (response.status === 404 || response.status === 405) {
          lastError = new Error(`Route ${path} is unavailable on ${base}`);
          continue;
        }
        return response;
      } catch (error) {
        lastError = error;
      }
    }
    throw lastError || new Error("Backend unavailable");
  }

  useEffect(() => {
    const fetchSlots = () => {
      apiFetch("/slots")
        .then((response) => response.json())
        .then((data) => setSlots(data.slots))
        .catch((error) => console.error("Error fetching slots:", error));
    };

    fetchSlots();
    const interval = setInterval(fetchSlots, 2000);
    return () => clearInterval(interval);
  }, []);

  const available = slots.filter((slot) => slot.status === "Available").length;

  function calculateEndTime(startTime, durationMinutes) {
    const [hours, minutes] = startTime.split(":").map(Number);
    const totalMinutes = hours * 60 + minutes + durationMinutes;
    const endHours = Math.floor(totalMinutes / 60);
    const endMinutes = totalMinutes % 60;

    return `${String(endHours).padStart(2, "0")}:${String(
      endMinutes
    ).padStart(2, "0")}`;
  }

  function formatDuration(minutes) {
    const hours = Math.floor(minutes / 60);
    const remainingMinutes = minutes % 60;

    if (hours === 0) {
      return `${remainingMinutes} minutes`;
    }

    if (remainingMinutes === 0) {
      return `${hours} hour${hours > 1 ? "s" : ""}`;
    }

    return `${hours} hour${hours > 1 ? "s" : ""} ${remainingMinutes} minutes`;
  }

  async function bookSlot() {
    if (!selectedSlot || !date || !time || !duration) {
      alert("Please select date, duration, and start time");
      return;
    }

    if (duration < 1 || duration > 1440) {
      alert("Duration must be between 1 and 1440 minutes");
      return;
    }

    try {
      const response = await apiFetch(
        `/book/${selectedSlot}?date=${encodeURIComponent(date)}&start_time=${encodeURIComponent(time)}&duration_minutes=${duration}`,
        {
          method: "POST",
        }
      );

      const data = await response.json().catch(() => ({}));

      if (response.ok && data.slot) {
        const endTime = calculateEndTime(time, duration);

        setMessage(
          `Booking Confirmed! Slot ${selectedSlot}, Date ${date}, ${time} - ${endTime}, Duration: ${formatDuration(duration)}`
        );

        setSelectedSlot(null);
        setDate("");
        setTime("");
        setDuration(60);
      } else {
        alert(`${data.message || `Booking failed (HTTP ${response.status})`}${data.next_available_time ? `\nNext Available Time: ${data.next_available_time}` : ""}`);
      }
    } catch (error) {
      console.error("Booking Error:", error);
      alert(`Unable to connect to backend. Check that FastAPI is running on port 8000 or 8001 and CORS allows this website. Details: ${error.message}`);
    }
  }

  async function cancelBooking(slotId) {
    try {
      const slot = slots.find((s) => s.id === slotId);

      if (!slot || !slot.date || !slot.start_time) {
        alert("Booking date and time not found");
        return;
      }

      const response = await apiFetch(
        `/cancel/${slotId}?date=${encodeURIComponent(slot.date)}&time=${encodeURIComponent(slot.start_time)}`,
        {
          method: "POST",
        }
      );

      const data = await response.json().catch(() => ({}));

      if (response.ok && data.slot) {
        setMessage(`Parking Slot ${slotId} booking cancelled successfully`);

        const updatedResponse = await apiFetch("/slots");
        const updatedData = await updatedResponse.json();
        setSlots(updatedData.slots);
      } else {
        alert(data.message);
      }
    } catch (error) {
      console.error("Cancel Booking Error:", error);
      alert(`Unable to connect to backend. Check that FastAPI is running on port 8000 or 8001 and CORS allows this website. Details: ${error.message}`);
    }
  }

  return (
    <div className="app">
      <header className="navbar">
        <h2>🚗 SmartPark</h2>
        <span>🟢 Live Parking Status</span>
      </header>

      <section className="hero">
        <h1>Find Your Parking Slot</h1>
        <p>Book your parking space easily and quickly.</p>
      </section>


      <section className="website-qr" style={{ textAlign: "center", padding: "24px", margin: "20px auto", background: "#fff", borderRadius: "16px", maxWidth: "420px", boxShadow: "0 4px 16px rgba(0,0,0,0.08)" }}>
        <h2>📱 Scan to Open SmartPark Website</h2>
        <p>Scan this QR code with your phone camera to open the live parking dashboard.</p>
        <div style={{ display: "inline-block", padding: "12px", background: "white", borderRadius: "12px" }}>
          <QRCodeSVG
            value="http://10.78.51.208:5175"
            size={220}
            level="H"
            includeMargin={true}
          />
        </div>
        <p style={{ overflowWrap: "anywhere", fontSize: "14px" }}>http://10.78.51.208:5175</p>
        <p style={{ fontSize: "13px", color: "#666" }}>Phone and laptop must be connected to the same Wi-Fi network.</p>
      </section>

      <section className="summary">
        <div className="summary-card">
          <h3>Total Slots</h3>
          <h1>{slots.length}</h1>
        </div>

        <div className="summary-card">
          <h3>Available Slots</h3>
          <h1>{available}</h1>
        </div>

        <div className="summary-card">
          <h3>Booked Slots</h3>
          <h1>{slots.length - available}</h1>
        </div>
      </section>

      {message && <div className="booking-message">{message}</div>}

      <section className="parking-section">
        <h2>Parking Slot Status</h2>

        <div className="slots">
          {slots.map((slot) => (
            <div
              className={`slot ${slot.status === "Available" ? "available" : "booked"}`}
              key={slot.id}
            >
              <h2>Parking Slot {slot.id}</h2>
              <div className="car">🚗</div>
              <h3>{slot.status}</h3>

              {slot.status === "Booked" && slot.date && (
                <p>
                  Latest Booking: {slot.date}
                  <br />
                  {slot.start_time} - {slot.end_time}
                </p>
              )}

              <button
                onClick={() => {
                  setSelectedSlot(slot.id);
                  setDate("");
                  setTime("");
                  setDuration(60);
                  setMessage("");
                }}
              >
                Book Slot
              </button>

              {slot.status === "Booked" && (
                <button className="cancel" onClick={() => cancelBooking(slot.id)}>
                  Cancel Latest Booking
                </button>
              )}
            </div>
          ))}
        </div>
      </section>

      {selectedSlot && (
        <section className="booking">
          <h2>Book Parking Slot {selectedSlot}</h2>

          <label>Select Date</label>
          <input
            type="date"
            min={new Date().toISOString().split("T")[0]}
            value={date}
            onChange={(e) => setDate(e.target.value)}
          />

          <label>Choose Your Duration (minutes)</label>
          <input
            type="number"
            min="1"
            max="1440"
            step="1"
            value={duration}
            onChange={(e) => setDuration(Number(e.target.value))}
            placeholder="Enter duration in minutes"
          />

          <p>Selected Duration: {formatDuration(duration || 0)}</p>

          <label>Select Start Time</label>
          <input
            type="time"
            value={time}
            onChange={(e) => setTime(e.target.value)}
          />

          {time && date && duration > 0 && (
            <div className="booking-details">
              <h3>Booking Summary</h3>
              <p>Date: {date}</p>
              <p>Start Time: {time}</p>
              <p>End Time: {calculateEndTime(time, duration)}</p>
              <p>Duration: {formatDuration(duration)}</p>
              <p>Buffer: 1 minute</p>
                </div>
          )}

          <button
            onClick={bookSlot}
            disabled={!date || !time || !duration || duration < 1 || duration > 1440}
          >
            Book Parking Slot
          </button>

          <button
            className="cancel"
            onClick={() => {
              setSelectedSlot(null);
              setDate("");
              setTime("");
            }}
          >
            Cancel
          </button>
        </section>
      )}

      <footer>
        <p>Smart Parking System | CSE Project</p>
      </footer>
    </div>
  );
}

export default App;
