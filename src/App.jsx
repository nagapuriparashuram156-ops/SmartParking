import React, { useEffect, useState, useCallback } from "react";
import { QRCodeSVG } from "qrcode.react";
import "./style.css";

const API_BASE_URL =
  import.meta.env.VITE_API_URL || "https://smartparking-backend-4ivz.onrender.com";
const WEBSITE_URL =
  import.meta.env.VITE_WEBSITE_URL || "https://smartparking-mobile.onrender.com";

function App() {
  const [slots, setSlots] = useState([]);
  const [summary, setSummary] = useState({
    total_slots: 3,
    available_now: 3,
    physically_occupied: 0,
    booked_reserved: 0,
    sensor_errors: 0,
  });
  const [bookings, setBookings] = useState([]);
  const [selectedSlot, setSelectedSlot] = useState(null);
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [duration, setDuration] = useState(60);
  const [message, setMessage] = useState(null);
  const [errorMsg, setErrorMsg] = useState(null);
  const [loading, setLoading] = useState(false);
  const [cancellingId, setCancellingId] = useState(null);
  const [backendStatus, setBackendStatus] = useState("connecting"); // "connected", "connecting", "error"
  const [lastRefreshed, setLastRefreshed] = useState(null);

  // Helper: Get today's date in YYYY-MM-DD
  const getTodayDateString = () => {
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, "0");
    const d = String(now.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  };

  // Helper: Calculate End Time based on start and duration minutes
  const calculateEndTime = (startTimeStr, durationMinutes) => {
    if (!startTimeStr || !durationMinutes) return "--:--";
    const parts = startTimeStr.split(":");
    if (parts.length < 2) return "--:--";
    const totalMinutes = parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10) + parseInt(durationMinutes, 10);
    const endH = Math.floor(totalMinutes / 60) % 24;
    const endM = totalMinutes % 60;
    return `${String(endH).padStart(2, "0")}:${String(endM).padStart(2, "0")}`;
  };

  // Helper: Format duration in human-readable string
  const formatDuration = (mins) => {
    if (!mins) return "0 min";
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    if (h > 0 && m > 0) return `${h}h ${m}m`;
    if (h > 0) return `${h} hr${h > 1 ? "s" : ""}`;
    return `${m} min`;
  };

  // API Fetch Wrapper
  const apiFetch = async (endpoint, options = {}) => {
    const url = `${API_BASE_URL}${endpoint}`;
    return fetch(url, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
    });
  };

  // Fetch Slots, Summary, and Bookings
  const refreshData = useCallback(async () => {
    try {
      const [slotsRes, bookingsRes] = await Promise.all([
        apiFetch("/slots"),
        apiFetch("/bookings"),
      ]);

      if (slotsRes.ok) {
        const slotsData = await slotsRes.json();
        setSlots(slotsData.slots || []);
        if (slotsData.summary) {
          setSummary(slotsData.summary);
        }
        setBackendStatus("connected");
        setLastRefreshed(new Date().toLocaleTimeString());
      } else {
        setBackendStatus("error");
      }

      if (bookingsRes.ok) {
        const bookingsData = await bookingsRes.json();
        setBookings(bookingsData.bookings || []);
      }
    } catch (err) {
      console.warn("Poll refresh failed:", err);
      setBackendStatus("error");
    }
  }, []);

  // Periodic polling every 3.5 seconds
  useEffect(() => {
    refreshData();
    const interval = setInterval(refreshData, 3500);
    return () => clearInterval(interval);
  }, [refreshData]);

  // Handle Create Booking
  const handleBookSlot = async (e) => {
    if (e) e.preventDefault();
    if (!selectedSlot || !date || !time || !duration) {
      setErrorMsg("Please select slot, date, start time, and duration.");
      return;
    }

    setLoading(true);
    setErrorMsg(null);
    setMessage(null);

    try {
      const query = new URLSearchParams({
        date: date,
        start_time: time,
        duration_minutes: String(duration),
      });

      const response = await apiFetch(`/book/${selectedSlot}?${query.toString()}`, {
        method: "POST",
      });

      const data = await response.json().catch(() => ({}));

      if (response.ok && data.booking) {
        const endTime = calculateEndTime(time, duration);
        setMessage(
          `Success! Slot ${selectedSlot} is reserved for ${date} from ${time} to ${endTime} (Booking ID: ${data.booking.booking_id}).`
        );
        setSelectedSlot(null);
        setDate("");
        setTime("");
        setDuration(60);
        await refreshData();
      } else {
        setErrorMsg(
          data.detail || data.message || `Booking failed with status ${response.status}.`
        );
      }
    } catch (err) {
      console.error("Booking error:", err);
      setErrorMsg("Failed to connect to backend service. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  // Handle Cancel Booking
  const handleCancelBooking = async (bookingId, slotId) => {
    if (!window.confirm(`Are you sure you want to cancel booking ${bookingId}?`)) {
      return;
    }

    setCancellingId(bookingId);
    setErrorMsg(null);
    setMessage(null);

    try {
      const response = await apiFetch(`/bookings/${bookingId}`, {
        method: "DELETE",
      });

      const data = await response.json().catch(() => ({}));

      if (response.ok) {
        setMessage(`Booking ${bookingId} for Slot ${slotId || ""} was cancelled successfully.`);
        await refreshData();
      } else {
        setErrorMsg(data.detail || data.message || "Failed to cancel booking.");
      }
    } catch (err) {
      console.error("Cancel error:", err);
      setErrorMsg("Connection error while cancelling booking.");
    } finally {
      setCancellingId(null);
    }
  };

  // Helper to open booking modal for a slot
  const openBookingModal = (slotId) => {
    setSelectedSlot(slotId);
    setDate(getTodayDateString());
    // Default time to current hour + 1 rounded
    const now = new Date();
    const h = String((now.getHours() + 1) % 24).padStart(2, "0");
    setTime(`${h}:00`);
    setDuration(60);
    setErrorMsg(null);
    setMessage(null);
  };

  return (
    <div className="app-container">
      {/* Top Navbar */}
      <header className="navbar">
        <div className="navbar-brand">
          <span className="brand-icon">🚗</span>
          <div>
            <h1 className="brand-title">SmartPark IoT</h1>
            <span className="brand-subtitle">ESP32 Ultrasonic • FastAPI • MySQL</span>
          </div>
        </div>

        <div className="navbar-status">
          <div className={`status-pill ${backendStatus}`}>
            <span className="status-dot"></span>
            {backendStatus === "connected" && "Cloud Backend Live"}
            {backendStatus === "connecting" && "Waking Backend..."}
            {backendStatus === "error" && "Backend Offline"}
          </div>
          {lastRefreshed && (
            <span className="refresh-caption">Synced {lastRefreshed}</span>
          )}
        </div>
      </header>

      {/* Backend Sleeping/Offline Notice */}
      {backendStatus === "error" && (
        <div className="banner error-banner">
          ⚠️ <strong>Cannot reach FastAPI backend:</strong> {API_BASE_URL}. If deployed on Render free tier, the instance may be spinning up from idle (takes ~30-50s). Please wait.
        </div>
      )}

      {/* Global Toast Messages */}
      {message && (
        <div className="banner success-banner">
          <span>✅ {message}</span>
          <button className="banner-close" onClick={() => setMessage(null)}>✕</button>
        </div>
      )}
      {errorMsg && (
        <div className="banner error-banner">
          <span>❌ {errorMsg}</span>
          <button className="banner-close" onClick={() => setErrorMsg(null)}>✕</button>
        </div>
      )}

      {/* Dashboard KPI Summary Cards */}
      <section className="kpi-grid">
        <div className="kpi-card">
          <div className="kpi-label">Total Slots</div>
          <div className="kpi-value">{summary.total_slots || 3}</div>
          <div className="kpi-subtext">Configured bays</div>
        </div>

        <div className="kpi-card kpi-green">
          <div className="kpi-label">Available Now</div>
          <div className="kpi-value">{summary.available_now}</div>
          <div className="kpi-subtext">No vehicle & no active reservation</div>
        </div>

        <div className="kpi-card kpi-red">
          <div className="kpi-label">Physically Occupied</div>
          <div className="kpi-value">{summary.physically_occupied}</div>
          <div className="kpi-subtext">ESP32 sensor detected vehicle</div>
        </div>

        <div className="kpi-card kpi-orange">
          <div className="kpi-label">Booked / Reserved</div>
          <div className="kpi-value">{summary.booked_reserved}</div>
          <div className="kpi-subtext">Active reservation right now</div>
        </div>

        <div className="kpi-card kpi-gray">
          <div className="kpi-label">Sensor Errors</div>
          <div className="kpi-value">{summary.sensor_errors}</div>
          <div className="kpi-subtext">Disconnected or timeout</div>
        </div>
      </section>

      {/* Real-Time Parking Slots Display */}
      <section className="section-block">
        <div className="section-header">
          <h2>🅿️ Live Parking Bays</h2>
          <span className="live-tag">Auto-refreshing via ESP32 telemetry</span>
        </div>

        <div className="slots-grid">
          {slots.map((slot) => {
            const isOccupied = slot.status === "Occupied";
            const isReserved = slot.status === "Reserved";
            const isError = slot.status === "Sensor Error";
            const isAvailable = slot.status === "Available";

            let cardTheme = "theme-available";
            let statusBadge = "Available";
            let carIcon = "🚗";

            if (isOccupied) {
              cardTheme = "theme-occupied";
              statusBadge = "Physically Occupied";
            } else if (isReserved) {
              cardTheme = "theme-reserved";
              statusBadge = "Reserved / Booked";
            } else if (isError) {
              cardTheme = "theme-error";
              statusBadge = "Sensor Error";
              carIcon = "⚠️";
            }

            return (
              <div key={slot.id} className={`slot-card ${cardTheme}`}>
                <div className="slot-card-header">
                  <span className="slot-number-badge">Bay #{slot.id}</span>
                  <span className={`badge-status ${cardTheme}`}>
                    {statusBadge}
                  </span>
                </div>

                <div className="slot-visual">
                  <span className="slot-car-icon">{carIcon}</span>
                  <div className="slot-main-status">{slot.status}</div>
                </div>

                {/* Sensor Telemetry Box */}
                <div className="telemetry-box">
                  <div className="telemetry-row">
                    <span className="tel-label">ESP32 Ultrasonic:</span>
                    <span className="tel-value">
                      {slot.physical_status === "Occupied" ? "Vehicle Detected" : slot.physical_status}
                    </span>
                  </div>
                  <div className="telemetry-row">
                    <span className="tel-label">Measured Distance:</span>
                    <span className="tel-value">
                      {slot.distance_cm != null && slot.distance_cm >= 0
                        ? `${slot.distance_cm.toFixed(1)} cm`
                        : "Out of range / N/A"}
                    </span>
                  </div>
                </div>

                {/* Reservation Details */}
                <div className="booking-info-box">
                  {slot.active_booking ? (
                    <div className="active-booking-info">
                      <div className="booking-tag active-tag">Active Reservation</div>
                      <p className="b-line"><strong>ID:</strong> {slot.active_booking.booking_id}</p>
                      <p className="b-line">
                        <strong>Time:</strong> {slot.active_booking.start_time} - {slot.active_booking.end_time} ({slot.active_booking.duration_minutes}m)
                      </p>
                    </div>
                  ) : slot.next_booking ? (
                    <div className="upcoming-booking-info">
                      <div className="booking-tag next-tag">Upcoming Reservation</div>
                      <p className="b-line">
                        {slot.next_booking.date} at {slot.next_booking.start_time}
                      </p>
                    </div>
                  ) : (
                    <div className="no-booking-info">
                      <span className="free-tag">No active reservations</span>
                    </div>
                  )}
                </div>

                {/* Action Buttons */}
                <div className="slot-card-actions">
                  <button
                    className="btn btn-book"
                    disabled={isOccupied}
                    onClick={() => openBookingModal(slot.id)}
                  >
                    {isOccupied ? "Occupied by Vehicle" : "Book Slot"}
                  </button>

                  {slot.active_booking && (
                    <button
                      className="btn btn-cancel"
                      disabled={cancellingId === slot.active_booking.booking_id}
                      onClick={() =>
                        handleCancelBooking(slot.active_booking.booking_id, slot.id)
                      }
                    >
                      {cancellingId === slot.active_booking.booking_id
                        ? "Cancelling..."
                        : "Cancel Active Booking"}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Booking Modal / Dialog */}
      {selectedSlot && (
        <div className="modal-backdrop" onClick={() => setSelectedSlot(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Reserve Parking Slot #{selectedSlot}</h3>
              <button className="modal-close-btn" onClick={() => setSelectedSlot(null)}>✕</button>
            </div>

            <form onSubmit={handleBookSlot} className="booking-form">
              <div className="form-group">
                <label>Date</label>
                <input
                  type="date"
                  min={getTodayDateString()}
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  required
                />
              </div>

              <div className="form-group">
                <label>Start Time (Asia/Kolkata)</label>
                <input
                  type="time"
                  value={time}
                  onChange={(e) => setTime(e.target.value)}
                  required
                />
              </div>

              <div className="form-group">
                <label>Duration (Minutes)</label>
                <div className="duration-presets">
                  {[30, 60, 120, 180].map((mins) => (
                    <button
                      type="button"
                      key={mins}
                      className={`preset-btn ${duration === mins ? "active" : ""}`}
                      onClick={() => setDuration(mins)}
                    >
                      {formatDuration(mins)}
                    </button>
                  ))}
                </div>
                <input
                  type="number"
                  min="1"
                  max="1440"
                  value={duration}
                  onChange={(e) => setDuration(Number(e.target.value))}
                  required
                />
              </div>

              {/* Calculated Summary Preview */}
              {date && time && duration > 0 && (
                <div className="booking-summary-preview">
                  <h4>Reservation Summary</h4>
                  <p><strong>Slot:</strong> Bay #{selectedSlot}</p>
                  <p><strong>Window:</strong> {date} • {time} to {calculateEndTime(time, duration)}</p>
                  <p><strong>Total Duration:</strong> {formatDuration(duration)}</p>
                  <p className="buffer-note">ℹ️ 1-minute buffer automatically enforced before and after</p>
                </div>
              )}

              <div className="modal-actions">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setSelectedSlot(null)}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={loading || !date || !time || duration < 1}
                >
                  {loading ? "Confirming..." : "Confirm Reservation"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Booking Management Section */}
      <section className="section-block">
        <div className="section-header">
          <h2>📋 Reservation Management</h2>
          <span className="live-tag">Total: {bookings.length} reservations</span>
        </div>

        {bookings.length === 0 ? (
          <div className="empty-state-box">
            No active or past reservations recorded in MySQL yet.
          </div>
        ) : (
          <div className="table-responsive">
            <table className="bookings-table">
              <thead>
                <tr>
                  <th>Booking ID</th>
                  <th>Slot</th>
                  <th>Date</th>
                  <th>Time Window</th>
                  <th>Duration</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {bookings.map((b) => (
                  <tr key={b.booking_id}>
                    <td>
                      <code className="booking-code">{b.booking_id}</code>
                    </td>
                    <td><strong>Bay #{b.slot_id}</strong></td>
                    <td>{b.booking_date}</td>
                    <td>{b.start_time?.slice(0, 5)} - {b.end_time?.slice(0, 5)}</td>
                    <td>{formatDuration(b.duration_minutes)}</td>
                    <td>
                      <span className={`status-tag status-${b.booking_status?.toLowerCase()}`}>
                        {b.booking_status}
                      </span>
                    </td>
                    <td>
                      {b.booking_status === "ACTIVE" ? (
                        <button
                          className="btn btn-table-cancel"
                          disabled={cancellingId === b.booking_id}
                          onClick={() => handleCancelBooking(b.booking_id, b.slot_id)}
                        >
                          {cancellingId === b.booking_id ? "..." : "Cancel"}
                        </button>
                      ) : (
                        <span className="cancelled-label">Cancelled</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Mobile QR Access Section */}
      <section className="qr-section">
        <div className="qr-card">
          <div className="qr-text">
            <h2>📱 Open SmartPark on Mobile</h2>
            <p>Scan with your smartphone camera to access live slot booking anywhere.</p>
            <a
              href={WEBSITE_URL}
              target="_blank"
              rel="noreferrer"
              className="qr-link"
            >
              {WEBSITE_URL} ↗
            </a>
            <div className="qr-sub">Public cloud deployment • No local Wi-Fi pairing required</div>
          </div>

          <div className="qr-code-box">
            <QRCodeSVG
              value={WEBSITE_URL}
              size={180}
              level="H"
              includeMargin={true}
            />
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="footer">
        <p>Smart Parking System • Hardware, Backend & Cloud Architecture • CSE Major Project</p>
      </footer>
    </div>
  );
}

export default App;
