
import React, { useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import "./style.css";

const API_URL = "https://smartparking-api-2026.onrender.com";
const WEBSITE_URL = "https://smartparking-39mz.onrender.com";

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
  const [loading, setLoading] = useState(false);
  const [connectionError, setConnectionError] = useState("");

  async function apiFetch(path, options = {}) {
    return fetch(`${API_URL}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
    });
  }

  async function fetchSlots() {
    try {
      const response = await apiFetch("/slots");

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data = await response.json();
      setSlots(data.slots || []);
      setConnectionError("");
    } catch (error) {
      console.error("Fetch slots error:", error);
      setConnectionError(
        "Cannot connect to backend. Please wait a moment and refresh."
      );
    }
  }

  useEffect(() => {
    fetchSlots();

    const timer = setInterval(fetchSlots, 5000);
    return () => clearInterval(timer);
  }, []);

  const available = slots.filter(
    (slot) => slot.status === "Available"
  ).length;

  function calculateEndTime(start, minutes) {
    const [h, m] = start.split(":").map(Number);
    const total = h * 60 + m + minutes;

    return `${String(Math.floor(total / 60) % 24).padStart(2, "0")}:${String(
      total % 60
    ).padStart(2, "0")}`;
  }

  function formatDuration(minutes) {
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;

    if (!hours) return `${rest} minutes`;
    if (!rest) return `${hours} hour${hours > 1 ? "s" : ""}`;

    return `${hours} hour${hours > 1 ? "s" : ""} ${rest} minutes`;
  }

  async function bookSlot() {
    if (!selectedSlot || !date || !time || !duration) {
      alert("Select date, start time, and duration.");
      return;
    }

    if (duration < 1 || duration > 1440) {
      alert("Duration must be from 1 to 1440 minutes.");
      return;
    }

    setLoading(true);

    try {
      const query = new URLSearchParams({
        date,
        start_time: time,
        duration_minutes: String(duration),
      });

      const response = await apiFetch(
        `/book/${selectedSlot}?${query.toString()}`,
        { method: "POST" }
      );

      const data = await response.json().catch(() => ({}));

      if (response.ok && data.booking) {
        setMessage(
          `Booking confirmed! Slot ${selectedSlot}, ${date}, ${time}–${calculateEndTime(
            time,
            duration
          )}, ${formatDuration(duration)}.`
        );

        setSelectedSlot(null);
        setDate("");
        setTime("");
        setDuration(60);

        await fetchSlots();
      } else {
        alert(
          data.detail ||
            data.message ||
            `Booking failed (HTTP ${response.status})`
        );
      }
    } catch (error) {
      console.error("Booking error:", error);
      alert(
        "Cannot connect to backend. Check internet connection and try again."
      );
    } finally {
      setLoading(false);
    }
  }

  async function cancelBooking(slotId) {
    const slot = slots.find((item) => item.id === slotId);

    if (!slot?.date || !slot?.start_time) {
      alert("Booking date/time was not found.");
      return;
    }

    if (!window.confirm(`Cancel latest booking for Slot ${slotId}?`)) {
      return;
    }

    try {
      const query = new URLSearchParams({
        date: slot.date,
        time: slot.start_time,
      });

      const response = await apiFetch(
        `/cancel/${slotId}?${query.toString()}`,
        { method: "POST" }
      );

      const data = await response.json().catch(() => ({}));

      if (response.ok) {
        setMessage(`Slot ${slotId} booking cancelled.`);
        await fetchSlots();
      } else {
        alert(data.detail || data.message || "Cancellation failed.");
      }
    } catch (error) {
      console.error("Cancellation error:", error);
      alert("Cannot connect to backend.");
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

      <section
        className="website-qr"
        style={{
          textAlign: "center",
          padding: "24px",
          margin: "20px auto",
          background: "#fff",
          borderRadius: "16px",
          maxWidth: "420px",
          boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
        }}
      >
        <h2>📱 Scan to Open SmartPark</h2>
        <p>Scan this QR code with your phone camera.</p>

        <div
          style={{
            display: "inline-block",
            padding: "12px",
            background: "white",
            borderRadius: "12px",
          }}
        >
          <QRCodeSVG
            value={WEBSITE_URL}
            size={220}
            level="H"
            includeMargin={true}
          />
        </div>

        <p style={{ overflowWrap: "anywhere", fontSize: "14px" }}>
          <a href={WEBSITE_URL} target="_blank" rel="noreferrer">
            {WEBSITE_URL}
          </a>
        </p>

        <p style={{ fontSize: "13px", color: "#666" }}>
          Accessible over the internet; same Wi-Fi is not required.
        </p>
      </section>

      {connectionError && (
        <div
          className="booking-message"
          style={{ background: "#fff0f0", color: "#9b1c1c" }}
        >
          {connectionError}
        </div>
      )}

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
          <h3>Booked / Occupied</h3>
          <h1>{slots.length - available}</h1>
        </div>
      </section>

      {message && <div className="booking-message">{message}</div>}

      <section className="parking-section">
        <h2>Parking Slot Status</h2>

        <div className="slots">
          {slots.map((slot) => (
            <div
              className={`slot ${
                slot.status === "Available" ? "available" : "booked"
              }`}
              key={slot.id}
            >
              <h2>Parking Slot {slot.id}</h2>
              <div className="car">🚗</div>
              <h3>{slot.status}</h3>

              {slot.date && slot.start_time && (
                <p>
                  Latest Booking: {slot.date}
                  <br />
                  {slot.start_time} - {slot.end_time}
                </p>
              )}

              <button
                disabled={slot.status !== "Available"}
                onClick={() => {
                  setSelectedSlot(slot.id);
                  setDate("");
                  setTime("");
                  setDuration(60);
                  setMessage("");
                }}
              >
                {slot.status === "Available" ? "Book Slot" : "Unavailable"}
              </button>

              {slot.status === "Booked" && slot.date && (
                <button
                  className="cancel"
                  onClick={() => cancelBooking(slot.id)}
                >
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
            min={new Date().toLocaleDateString("en-CA")}
            value={date}
            onChange={(e) => setDate(e.target.value)}
          />

          <label>Choose Duration (minutes)</label>
          <input
            type="number"
            min="1"
            max="1440"
            step="1"
            value={duration}
            onChange={(e) => setDuration(Number(e.target.value))}
          />

          <p>Selected Duration: {formatDuration(duration || 0)}</p>

          <label>Select Start Time</label>
          <input
            type="time"
            value={time}
            onChange={(e) => setTime(e.target.value)}
          />

          {date && time && duration > 0 && (
            <div className="booking-details">
              <h3>Booking Summary</h3>
              <p>Date: {date}</p>
              <p>Start Time: {time}</p>
              <p>End Time: {calculateEndTime(time, duration)}</p>
              <p>Duration: {formatDuration(duration)}</p>
              <p>Buffer: 1 minute between bookings</p>
            </div>
          )}

          <button
            disabled={loading || !date || !time || duration < 1 || duration > 1440}
            onClick={bookSlot}
          >
            {loading ? "Booking..." : "Confirm Booking"}
          </button>

          <button
            className="cancel"
            onClick={() => {
              setSelectedSlot(null);
              setDate("");
              setTime("");
            }}
          >
            Close
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
