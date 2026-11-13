import React, { useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import "./style.css";

const API_URL = "https://smartparking-api-2026.onrender.com";
const WEBSITE_URL = "https://smartparking-39mz.onrender.com";

function App() {
  const [slots, setSlots] = useState([{id:1,status:"Available"},{id:2,status:"Available"},{id:3,status:"Available"}]);
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
      headers: {"Content-Type":"application/json", ...(options.headers || {})}
    });
  }

  async function fetchSlots() {
    try {
      const response = await apiFetch("/slots");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setSlots(data.slots || []);
      setConnectionError("");
    } catch (error) {
      console.error("Fetch slots error:", error);
      setConnectionError("Unable to connect to backend. It may be waking up; refresh shortly.");
    }
  }

  useEffect(() => {
    fetchSlots();
    const timer = setInterval(fetchSlots, 5000);
    return () => clearInterval(timer);
  }, []);

  const available = slots.filter(slot => slot.status === "Available").length;

  function calculateEndTime(start, minutes) {
    const [h, m] = start.split(":").map(Number);
    const total = h * 60 + m + minutes;
    return `${String(Math.floor(total / 60) % 24).padStart(2,"0")}:${String(total % 60).padStart(2,"0")}`;
  }

  function formatDuration(minutes) {
    const hours = Math.floor(minutes / 60), rest = minutes % 60;
    if (!hours) return `${rest} minutes`;
    if (!rest) return `${hours} hour${hours > 1 ? "s" : ""}`;
    return `${hours} hour${hours > 1 ? "s" : ""} ${rest} minutes`;
  }

  async function bookSlot() {
    if (!selectedSlot || !date || !time || !duration) return alert("Select date, start time, and duration.");
    if (duration < 1 || duration > 1440) return alert("Duration must be from 1 to 1440 minutes.");
    setLoading(true);
    try {
      const query = new URLSearchParams({date, start_time:time, duration_minutes:String(duration)});
      const response = await apiFetch(`/book/${selectedSlot}?${query}`, {method:"POST"});
      const data = await response.json().catch(() => ({}));
      if (response.ok && data.slot) {
        setMessage(`Booking confirmed! Slot ${selectedSlot}, ${date}, ${time}–${calculateEndTime(time,duration)}, ${formatDuration(duration)}.`);
        setSelectedSlot(null); setDate(""); setTime(""); setDuration(60);
        await fetchSlots();
      } else {
        alert(data.detail || data.message || `Booking failed (HTTP ${response.status})`);
      }
    } catch (error) {
      console.error(error);
      alert("Unable to connect to Render backend. Check internet and try again.");
    } finally { setLoading(false); }
  }

  async function cancelBooking(slotId) {
    const slot = slots.find(item => item.id === slotId);
    if (!slot?.date || !slot?.start_time) return alert("Booking date/time was not found.");
    if (!window.confirm(`Cancel latest booking for Slot ${slotId}?`)) return;
    try {
      const query = new URLSearchParams({date:slot.date, time:slot.start_time});
      const response = await apiFetch(`/cancel/${slotId}?${query}`, {method:"POST"});
      const data = await response.json().catch(() => ({}));
      if (response.ok) { setMessage(`Slot ${slotId} booking cancelled.`); await fetchSlots(); }
      else alert(data.detail || data.message || "Cancellation failed.");
    } catch (error) { console.error(error); alert("Unable to connect to backend."); }
  }

  return <div className="app">
    <header className="navbar"><h2>🚗 SmartPark</h2><span>🟢 Live Parking Status</span></header>
    <section className="hero"><h1>Find Your Parking Slot</h1><p>Book your parking space easily and quickly.</p></section>
