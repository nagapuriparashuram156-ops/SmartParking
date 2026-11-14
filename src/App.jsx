import React, { useEffect, useState, useCallback, useMemo } from "react";
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
    physical_available: 3,
    available_now: 3,
    physically_occupied: 0,
    booked_reserved: 0,
    sensor_errors: 0,
    upcoming_reservations: 0,
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
  const [backendStatus, setBackendStatus] = useState("connecting");
  const [lastRefreshed, setLastRefreshed] = useState(null);
  const [confirmedBooking, setConfirmedBooking] = useState(null);
  const [showCancelledHistory, setShowCancelledHistory] = useState(false);

  // Helper: Get today's date in YYYY-MM-DD format (Asia/Kolkata local)
  const getTodayDateString = () => {
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, "0");
    const d = String(now.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  };

  // Helper: Calculate End Time based on start time and duration minutes
  const calculateEndTime = (startTimeStr, durationMinutes) => {
    if (!startTimeStr || !durationMinutes) return "--:--";
    const parts = startTimeStr.split(":");
    if (parts.length < 2) return "--:--";
    const totalMinutes =
      parseInt(parts[0], 10) * 60 +
      parseInt(parts[1], 10) +
      parseInt(durationMinutes, 10);
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

  // Helper: Check if a booking window is in the past, active now, or upcoming
  const getBookingLifecycleStatus = (booking) => {
    if (booking.booking_status === "CANCELLED") return "CANCELLED";

    try {
      const now = new Date();
      const startParts = (booking.start_time || "").slice(0, 5).split(":");
      const endParts = (booking.end_time || "").slice(0, 5).split(":");

      const [y, m, d] = (booking.booking_date || "").split("-").map(Number);
      if (!y || !m || !d || startParts.length < 2 || endParts.length < 2) {
        return booking.booking_status;
      }

      const startDt = new Date(y, m - 1, d, Number(startParts[0]), Number(startParts[1]), 0);
      let endDt = new Date(y, m - 1, d, Number(endParts[0]), Number(endParts[1]), 0);

      // Handle midnight crossing where end time is on the next calendar day
      if (endDt <= startDt) {
        endDt = new Date(y, m - 1, d + 1, Number(endParts[0]), Number(endParts[1]), 0);
      }

      if (now >= startDt && now < endDt) {
        return "ACTIVE_NOW";
      } else if (now < startDt) {
        return "UPCOMING";
      } else {
        return "COMPLETED";
      }
    } catch {
      return booking.booking_status;
    }
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

  // Periodic polling every 3.5 seconds for live telemetry
  useEffect(() => {
    refreshData();
    const interval = setInterval(refreshData, 3500);
    return () => clearInterval(interval);
  }, [refreshData]);

  // Dashboard KPI Counters: Fully separated metrics per Section 2, 3, 5 & 9
  const displaySummary = useMemo(() => {
    const total = slots.length > 0 ? slots.length : (summary.total_slots || 3);
    const occupied = slots.filter((s) => s.is_physically_occupied).length;
    const physicalAvailable = total - occupied;

    // BOOKED / RESERVED KPI (Section 2 Specification):
    // Number of reservation records that:
    // - Are confirmed and not cancelled
    // - Have an end datetime later than current time
    // - Include BOTH upcoming reservations and currently active reservations
    // - Exclude cancelled bookings
    // - Exclude bookings that have already ended
    let reservedCount = 0;
    if (bookings && bookings.length > 0) {
      reservedCount = bookings.filter((b) => {
        if (b.booking_status === "CANCELLED") return false;
        const lifeStatus = getBookingLifecycleStatus(b);
        return lifeStatus === "ACTIVE_NOW" || lifeStatus === "UPCOMING";
      }).length;
    } else {
      reservedCount = summary.booked_reserved || 0;
    }

    const errors = slots.filter(
      (s) => s.status === "Sensor Error" || s.physical_status === "Error"
    ).length;

    // Available for Booking KPI:
    // Number of physical parking bays that remain available for at least some bookable time during the day.
    // 3 physical parking bays total.
    // An hourly reservation does NOT make a bay unavailable for the whole day.
    // Only decreases when a bay is booked for the entire day.
    let availableForBookingCount = 3;
    if (slots && slots.length > 0) {
      availableForBookingCount = slots.filter((s) => s.is_bookable_today !== false).length;
    } else if (summary.available_for_booking != null) {
      availableForBookingCount = summary.available_for_booking;
    } else if (summary.available_now != null) {
      availableForBookingCount = summary.available_now;
    }

    return {
      total_slots: total,
      physical_available: physicalAvailable,
      physically_occupied: occupied,
      booked_reserved: reservedCount,
      available_for_booking: availableForBookingCount,
      available_now: availableForBookingCount,
      sensor_errors: errors,
    };
  }, [slots, bookings, summary]);

  // Existing active bookings for selected slot on chosen date (handling midnight)
  const slotBookingsOnDate = useMemo(() => {
    if (!selectedSlot || !date) return [];
    return bookings.filter(
      (b) =>
        Number(b.slot_id) === Number(selectedSlot) &&
        b.booking_status === "ACTIVE" &&
        (String(b.booking_date).slice(0, 10) === String(date).slice(0, 10) ||
         String(b.start_datetime || "").slice(0, 10) === String(date).slice(0, 10) ||
         String(b.end_datetime || "").slice(0, 10) === String(date).slice(0, 10))
    );
  }, [selectedSlot, date, bookings]);

  // Calculate available time windows & next available time for selected bay on selected date
  const availabilityAnalysis = useMemo(() => {
    if (!selectedSlot || !date) {
      return { availableWindows: [], nextAvailableTime: "Select date", isFull: false };
    }

    const BUFFER_MINUTES = 1;
    const blocked = [];

    for (const b of slotBookingsOnDate) {
      const sParts = (b.start_time || "").slice(0, 5).split(":").map(Number);
      const eParts = (b.end_time || "").slice(0, 5).split(":").map(Number);
      if (sParts.length < 2 || eParts.length < 2) continue;

      const sMin = sParts[0] * 60 + sParts[1];
      let eMin = eParts[0] * 60 + eParts[1];
      if (eMin <= sMin) {
        eMin = sMin + (Number(b.duration_minutes) || 60);
      }
      eMin = Math.min(1440, eMin);

      // Add 1-minute buffer to blocked interval
      blocked.push([Math.max(0, sMin - BUFFER_MINUTES), Math.min(1440, eMin + BUFFER_MINUTES)]);
    }

    // Merge overlapping blocks
    blocked.sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const [start, end] of blocked) {
      if (!merged.length || merged[merged.length - 1][1] < start) {
        merged.push([start, end]);
      } else {
        merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], end);
      }
    }

    // Find free windows
    const windows = [];
    let curMin = 0;
    for (const [bStart, bEnd] of merged) {
      if (bStart > curMin) {
        const sH = String(Math.floor(curMin / 60)).padStart(2, "0");
        const sM = String(curMin % 60).padStart(2, "0");
        const eH = String(Math.floor(bStart / 60)).padStart(2, "0");
        const eM = String(bStart % 60).padStart(2, "0");
        windows.push({
          start: `${sH}:${sM}`,
          end: `${eH}:${eM}`,
          duration: bStart - curMin,
        });
      }
      curMin = Math.max(curMin, bEnd);
    }

    if (curMin < 1440) {
      const sH = String(Math.floor(curMin / 60)).padStart(2, "0");
      const sM = String(curMin % 60).padStart(2, "0");
      windows.push({
        start: `${sH}:${sM}`,
        end: "23:59",
        duration: 1440 - curMin,
      });
    }

    // Compute next available time
    let nextAvail = "Available Now";
    const today = getTodayDateString();

    if (date === today) {
      const now = new Date();
      const nowMin = now.getHours() * 60 + now.getMinutes();

      let blockedUntil = null;
      for (const [bStart, bEnd] of merged) {
        if (bStart <= nowMin && nowMin < bEnd) {
          blockedUntil = bEnd;
          break;
        }
      }

      if (blockedUntil !== null) {
        if (blockedUntil < 1440) {
          const h = String(Math.floor(blockedUntil / 60)).padStart(2, "0");
          const m = String(blockedUntil % 60).padStart(2, "0");
          nextAvail = `Today at ${h}:${m}`;
        } else {
          nextAvail = "Fully booked for today";
        }
      } else {
        nextAvail = "Available Now";
      }
    } else {
      if (windows.length > 0) {
        nextAvail = `${windows[0].start} onwards`;
      } else {
        nextAvail = "No available windows";
      }
    }

    return {
      availableWindows: windows,
      nextAvailableTime: nextAvail,
      isFull: windows.length === 0,
    };
  }, [selectedSlot, date, slotBookingsOnDate]);

  // Client-side conflict check for selected time window + 1-minute buffer
  const conflictWarning = useMemo(() => {
    if (!selectedSlot || !date || !time || !duration) return null;
    if (slotBookingsOnDate.length === 0) return null;

    const [reqH, reqM] = time.split(":").map(Number);
    if (isNaN(reqH) || isNaN(reqM)) return null;
    const reqStart = reqH * 60 + reqM;
    const reqEnd = reqStart + Number(duration);

    for (const b of slotBookingsOnDate) {
      const bStartParts = (b.start_time || "").slice(0, 5).split(":").map(Number);
      const bEndParts = (b.end_time || "").slice(0, 5).split(":").map(Number);
      if (bStartParts.length < 2 || bEndParts.length < 2) continue;

      const bStart = bStartParts[0] * 60 + bStartParts[1];
      let bEnd = bEndParts[0] * 60 + bEndParts[1];
      if (bEnd <= bStart) {
        bEnd = bStart + (Number(b.duration_minutes) || 60);
      }

      // 1-minute safety buffer check
      const bufStart = bStart - 1;
      const bufEnd = bEnd + 1;

      if (reqStart < bufEnd && reqEnd > bufStart) {
        return `Requested window (${time}–${calculateEndTime(time, duration)}) overlaps with existing reservation ${b.start_time?.slice(0, 5)}–${b.end_time?.slice(0, 5)} (enforcing 1-minute safety buffer).`;
      }
    }
    return null;
  }, [selectedSlot, date, time, duration, slotBookingsOnDate]);

  // Handle Create Booking
  const handleBookSlot = async (e) => {
    if (e) e.preventDefault();
    if (!selectedSlot || !date || !time || !duration) {
      setErrorMsg("Please select slot, date, start time, and duration.");
      return;
    }

    if (conflictWarning) {
      setErrorMsg(conflictWarning);
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
        setConfirmedBooking({
          booking_id: data.booking.booking_id,
          slot_id: selectedSlot,
          date: date,
          start_time: time,
          end_time: endTime,
          duration: duration,
        });
        setMessage(
          `Success! Bay #${selectedSlot} reserved: ${date} from ${time} to ${endTime} (Booking ID: ${data.booking.booking_id}).`
        );
        setSelectedSlot(null);

        // IMMEDIATE SYNCHRONOUS STATE UPDATE
        if (data.booking) {
          setBookings((prev) => [
            data.booking,
            ...prev.filter((b) => b.booking_id !== data.booking.booking_id),
          ]);
        }
        if (data.summary) {
          setSummary(data.summary);
        }
        if (data.slot) {
          setSlots((prev) =>
            prev.map((s) => (s.id === data.slot.id ? data.slot : s))
          );
        }

        // Full authoritative background sync
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
    if (!window.confirm(`Are you sure you want to cancel reservation ${bookingId}?`)) {
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
        setMessage(
          `Reservation ${bookingId} for Bay #${slotId || ""} was cancelled successfully. That time window is now free!`
        );

        // IMMEDIATE SYNCHRONOUS STATE UPDATE:
        // Update local booking list to CANCELLED so active table & KPI update instantly
        setBookings((prev) =>
          prev.map((b) =>
            b.booking_id === bookingId ? { ...b, booking_status: "CANCELLED" } : b
          )
        );
        if (data.summary) {
          setSummary(data.summary);
        }

        // Full authoritative background sync
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

  // Helper to open booking modal with smart, conflict-free initial time
  const openBookingModal = (slotId) => {
    setSelectedSlot(slotId);
    const today = getTodayDateString();
    setDate(today);

    // Active bookings for this slot on today
    const slotBookings = bookings.filter(
      (b) =>
        Number(b.slot_id) === Number(slotId) &&
        String(b.booking_date).slice(0, 10) === today &&
        b.booking_status === "ACTIVE"
    );

    // Calculate smart initial start time that is conflict-free
    const now = new Date();
    const curHour = now.getHours();
    let initialTime = `${String((curHour + 1) % 24).padStart(2, "0")}:00`;
    let initialDuration = 60;

    // Check availability windows
    if (slotBookings.length > 0) {
      let maxEndMin = 0;
      for (const b of slotBookings) {
        const eParts = (b.end_time || "").slice(0, 5).split(":").map(Number);
        if (eParts.length >= 2) {
          const eMin = eParts[0] * 60 + eParts[1];
          if (eMin > maxEndMin) maxEndMin = eMin;
        }
      }
      const nextSafeStart = maxEndMin + 1; // 1-minute buffer
      if (nextSafeStart < 1440) {
        const nextH = Math.floor(nextSafeStart / 60);
        const nextM = nextSafeStart % 60;
        initialTime = `${String(nextH).padStart(2, "0")}:${String(nextM).padStart(2, "0")}`;
        initialDuration = Math.min(60, 1440 - nextSafeStart);
      }
    }

    setTime(initialTime);
    setDuration(initialDuration > 0 ? initialDuration : 30);
    setErrorMsg(null);
    setMessage(null);
    setConfirmedBooking(null);
  };

  // Filter bookings for the Reservation Management table
  // Requirement: Cancelled bookings disappear from active management table,
  // but remain stored in MySQL for full audit history.
  const activeBookingsList = useMemo(() => {
    return bookings.filter((b) => b.booking_status !== "CANCELLED");
  }, [bookings]);

  const cancelledBookingsList = useMemo(() => {
    return bookings.filter((b) => b.booking_status === "CANCELLED");
  }, [bookings]);

  return (
    <div className="app-container">
      {/* Top Navigation Bar */}
      <header className="navbar">
        <div className="navbar-brand">
          <span className="brand-icon">🚗</span>
          <div>
            <h1 className="brand-title">SmartPark IoT</h1>
            <span className="brand-subtitle">ESP32 Ultrasonic • Dual Red/Green LEDs • FastAPI • MySQL</span>
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
          ⚠️ <strong>Cannot reach FastAPI backend:</strong> {API_BASE_URL}. If hosted on Render free tier, the web service may be waking up from idle (~30-50s). Please wait.
        </div>
      )}

      {/* Global Alerts & Toast Banners */}
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

      {/* Booking Confirmation Dialog */}
      {confirmedBooking && (
        <div className="confirmation-card">
          <h3>🎉 Reservation Confirmed!</h3>
          <p>Your parking bay has been successfully reserved in MySQL.</p>
          <div className="booking-id-pill">{confirmedBooking.booking_id}</div>
          <div className="conf-details">
            <p><strong>Parking Bay:</strong> Bay #{confirmedBooking.slot_id}</p>
            <p><strong>Date:</strong> {confirmedBooking.date}</p>
            <p><strong>Reserved Window:</strong> {confirmedBooking.start_time} to {confirmedBooking.end_time} ({formatDuration(confirmedBooking.duration)})</p>
            <p><strong>Safety Buffer:</strong> 1-minute buffer enforced before and after</p>
          </div>
          <button className="btn btn-primary" onClick={() => setConfirmedBooking(null)}>
            Dismiss & Return to Dashboard
          </button>
        </div>
      )}

      {/* Dashboard KPI Summary Cards (Independently Separated Metrics) */}
      <section className="kpi-grid">
        <div className="kpi-card">
          <div className="kpi-label">Total Slots</div>
          <div className="kpi-value">{displaySummary.total_slots || 3}</div>
          <div className="kpi-subtext">Total configured bays</div>
        </div>

        <div className="kpi-card kpi-green">
          <div className="kpi-label">Physical Available</div>
          <div className="kpi-value">{displaySummary.physical_available}</div>
          <div className="kpi-subtext">Bays with no vehicle present</div>
        </div>

        <div className="kpi-card kpi-red">
          <div className="kpi-label">Physical Occupied</div>
          <div className="kpi-value">{displaySummary.physically_occupied}</div>
          <div className="kpi-subtext">ESP32 sensor detected vehicles</div>
        </div>

        <div className="kpi-card kpi-orange">
          <div className="kpi-label">Booked / Reserved</div>
          <div className="kpi-value">{displaySummary.booked_reserved}</div>
          <div className="kpi-subtext">Active + Upcoming Reservations</div>
        </div>

        <div className="kpi-card kpi-blue">
          <div className="kpi-label">Available for Booking</div>
          <div className="kpi-value">{displaySummary.available_for_booking}</div>
          <div className="kpi-subtext">Bays with open time slots today</div>
        </div>

        <div className="kpi-card kpi-gray">
          <div className="kpi-label">Sensor Errors</div>
          <div className="kpi-value">{displaySummary.sensor_errors}</div>
          <div className="kpi-subtext">Hardware error or timeout</div>
        </div>
      </section>

      {/* Live Parking Bays Grid */}
      <section className="section-block">
        <div className="section-header">
          <h2>🅿️ Live Parking Bays</h2>
          <span className="live-tag">
            ESP32 Ultrasonic Telemetry • Dual Red/Green LED Feedback
          </span>
        </div>

        <div className="slots-grid">
          {slots.map((slot) => {
            const isPhysicallyOccupied = slot.is_physically_occupied || slot.physical_status === "Occupied";
            const isSensorError = slot.status === "Sensor Error" || slot.physical_status === "Error";
            const isCurrentlyReserved = slot.is_currently_reserved;
            const hasUpcomingReservation = slot.has_upcoming_reservation;

            // Display Theme Logic:
            // 1. Physically Occupied: Sensor detects vehicle (takes highest physical priority) -> Red
            // 2. Sensor Error: Hardware error -> Gray
            // 3. Reserved: Active reservation right now, empty -> Orange
            // 4. Upcoming Reservation: Future reservation exists, empty now -> Blue
            // 5. Available: Clear, empty, ready for booking -> Green
            let cardTheme = "theme-available";
            let statusBadge = "Available";
            let mainStatus = "Available";
            let carIcon = "🅿️";

            if (isPhysicallyOccupied) {
              cardTheme = "theme-occupied";
              statusBadge = "Physically Occupied";
              mainStatus = "Occupied";
              carIcon = "🚗";
            } else if (isSensorError) {
              cardTheme = "theme-error";
              statusBadge = "Sensor Error";
              mainStatus = "Sensor Error";
              carIcon = "⚠️";
            } else if (isCurrentlyReserved) {
              cardTheme = "theme-reserved";
              statusBadge = "Reserved";
              mainStatus = "Reserved";
              carIcon = "🔒";
            } else if (hasUpcomingReservation) {
              cardTheme = "theme-upcoming";
              statusBadge = "Upcoming Reservation";
              mainStatus = "Upcoming Reservation";
              carIcon = "🕒";
            }

            const activeOrUpcomingBooking = slot.active_booking || slot.next_booking;

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
                  <div className="slot-main-status">{mainStatus}</div>
                  <div className="slot-availability-caption">
                    {slot.availability_window || "Available for booking"}
                  </div>
                </div>

                {/* Sensor Telemetry Box */}
                <div className="telemetry-box">
                  <div className="telemetry-row">
                    <span className="tel-label">ESP32 Status:</span>
                    <span className="tel-value">
                      {isPhysicallyOccupied
                        ? "Vehicle Detected (RED LED ON)"
                        : isSensorError
                        ? "Sensor Error / Offline (LEDS OFF)"
                        : "Bay Empty (GREEN LED ON)"}
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

                {/* Reservation Details Box */}
                <div className="booking-info-box">
                  {slot.active_booking ? (
                    <div className="active-booking-info">
                      <div className="booking-tag active-tag">
                        Active Reservation {isPhysicallyOccupied ? "(Vehicle in Bay)" : ""}
                      </div>
                      <p className="b-line"><strong>ID:</strong> {slot.active_booking.booking_id}</p>
                      <p className="b-line">
                        <strong>Time:</strong> {slot.active_booking.start_time} - {slot.active_booking.end_time} ({formatDuration(slot.active_booking.duration_minutes)})
                      </p>
                      {slot.upcoming_count > 0 && (
                        <p className="b-line" style={{ fontSize: "11px", color: "#1d4ed8", marginTop: "3px" }}>
                          + {slot.upcoming_count} upcoming reservation{slot.upcoming_count > 1 ? "s" : ""}
                        </p>
                      )}
                    </div>
                  ) : slot.next_booking ? (
                    <div className="upcoming-booking-info">
                      <div className="booking-tag next-tag">
                        Upcoming Reservation {slot.upcoming_count > 1 ? `(1 of ${slot.upcoming_count})` : ""}
                      </div>
                      <p className="b-line"><strong>ID:</strong> {slot.next_booking.booking_id}</p>
                      <p className="b-line">
                        <strong>Time:</strong> {slot.next_booking.date} at {slot.next_booking.start_time} - {slot.next_booking.end_time}
                      </p>
                      {slot.upcoming_count > 1 && (
                        <p className="b-line" style={{ fontSize: "11px", color: "#1d4ed8", marginTop: "3px" }}>
                          + {slot.upcoming_count - 1} more reservation{slot.upcoming_count > 2 ? "s" : ""} scheduled
                        </p>
                      )}
                    </div>
                  ) : (
                    <div className="no-booking-info">
                      <span className="free-tag">No reservations • Free for booking</span>
                    </div>
                  )}
                </div>

                {/* Action Buttons */}
                <div className="slot-card-actions">
                  <button
                    className="btn btn-book"
                    onClick={() => openBookingModal(slot.id)}
                  >
                    {isPhysicallyOccupied
                      ? "Reserve Future Window"
                      : isCurrentlyReserved
                      ? "Reserve Later Window"
                      : "Book Slot"}
                  </button>

                  {activeOrUpcomingBooking && (
                    <button
                      className="btn btn-cancel"
                      disabled={cancellingId === activeOrUpcomingBooking.booking_id}
                      onClick={() =>
                        handleCancelBooking(activeOrUpcomingBooking.booking_id, slot.id)
                      }
                    >
                      {cancellingId === activeOrUpcomingBooking.booking_id
                        ? "Cancelling..."
                        : `Cancel Booking (${activeOrUpcomingBooking.booking_id})`}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Booking Form Modal Dialog */}
      {selectedSlot && (
        <div className="modal-backdrop" onClick={() => setSelectedSlot(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Reserve Parking Slot</h3>
              <button className="modal-close-btn" onClick={() => setSelectedSlot(null)}>✕</button>
            </div>

            <form onSubmit={handleBookSlot} className="booking-form">
              {/* Bay Selection Dropdown */}
              <div className="form-group">
                <label>Select Parking Bay</label>
                <select
                  value={selectedSlot}
                  onChange={(e) => setSelectedSlot(Number(e.target.value))}
                >
                  <option value={1}>Bay #1</option>
                  <option value={2}>Bay #2</option>
                  <option value={3}>Bay #3</option>
                </select>
              </div>

              {/* Booking Date */}
              <div className="form-group">
                <label>Booking Date</label>
                <input
                  type="date"
                  min={getTodayDateString()}
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  required
                />
              </div>

              {/* Availability & Next Available Time Display */}
              <div className="slot-day-intervals">
                <div className="next-avail-badge">
                  ⏱️ Next Available: {availabilityAnalysis.nextAvailableTime}
                </div>

                <span className="intervals-title">
                  Time Windows for Bay #{selectedSlot} on {date || "selected date"}:
                </span>

                {slotBookingsOnDate.length === 0 ? (
                  <p className="no-conflicts-note">
                    ✅ No existing bookings. All hours are open for reservation!
                  </p>
                ) : (
                  <>
                    <ul className="intervals-list">
                      {slotBookingsOnDate.map((b) => (
                        <li key={b.booking_id}>
                          🔒 <strong>{b.start_time?.slice(0, 5)} – {b.end_time?.slice(0, 5)}</strong> (Reserved • ID: {b.booking_id})
                        </li>
                      ))}
                    </ul>

                    {availabilityAnalysis.availableWindows.length > 0 && (
                      <div>
                        <span style={{ fontSize: "11px", fontWeight: "700", color: "#166534" }}>
                          Available Open Windows (Click to select start time):
                        </span>
                        <div className="windows-chips-container">
                          {availabilityAnalysis.availableWindows.map((w, idx) => (
                            <button
                              type="button"
                              key={idx}
                              className="window-chip available"
                              onClick={() => {
                                setTime(w.start);
                                if (w.duration && w.duration > 0 && duration > w.duration) {
                                  setDuration(Math.min(w.duration, 60));
                                }
                              }}
                            >
                              🟢 {w.start} – {w.end}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}
                <p className="buffer-rule-note">
                  ℹ️ 1-minute safety buffer is enforced between bookings. Multiple non-overlapping bookings per day are fully supported.
                </p>
              </div>

              {/* Start Time */}
              <div className="form-group">
                <label>Start Time (Asia/Kolkata)</label>
                <input
                  type="time"
                  value={time}
                  onChange={(e) => setTime(e.target.value)}
                  required
                />
              </div>

              {/* Duration with Presets */}
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

              {/* Real-time Overlap Conflict Banner */}
              {conflictWarning && (
                <div className="modal-conflict-warning">
                  ⚠️ {conflictWarning}
                </div>
              )}

              {/* Calculated Summary Preview */}
              {date && time && duration > 0 && !conflictWarning && (
                <div className="booking-summary-preview">
                  <h4>Reservation Summary</h4>
                  <p><strong>Bay:</strong> Bay #{selectedSlot}</p>
                  <p><strong>Window:</strong> {date} • {time} to {calculateEndTime(time, duration)}</p>
                  <p><strong>Duration:</strong> {formatDuration(duration)}</p>
                  <p className="buffer-note">✅ Requested window is open and conflict-free!</p>
                </div>
              )}

              {/* Modal Actions */}
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
                  disabled={loading || !date || !time || duration < 1 || Boolean(conflictWarning)}
                >
                  {loading ? "Confirming..." : "Confirm Reservation"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Reservation Management Table */}
      <section className="section-block">
        <div className="section-header">
          <div>
            <h2>📋 Active Reservation Management</h2>
            <span className="live-tag">
              Active & Upcoming: {activeBookingsList.length} | Total in DB: {bookings.length}
            </span>
          </div>

          {cancelledBookingsList.length > 0 && (
            <button
              className="btn btn-secondary"
              style={{ width: "auto", fontSize: "12px", padding: "6px 14px" }}
              onClick={() => setShowCancelledHistory(!showCancelledHistory)}
            >
              {showCancelledHistory
                ? "Hide Cancelled History"
                : `Show Cancelled History (${cancelledBookingsList.length})`}
            </button>
          )}
        </div>

        {activeBookingsList.length === 0 && !showCancelledHistory ? (
          <div className="empty-state-box">
            No active or upcoming reservations recorded in MySQL. All bays are available!
          </div>
        ) : (
          <div className="table-responsive">
            <table className="bookings-table">
              <thead>
                <tr>
                  <th>Booking ID</th>
                  <th>Slot / Bay</th>
                  <th>Date</th>
                  <th>Time Window</th>
                  <th>Duration</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {(showCancelledHistory ? bookings : activeBookingsList).map((b) => {
                  const lifeStatus = getBookingLifecycleStatus(b);
                  const isCancelled = b.booking_status === "CANCELLED" || lifeStatus === "CANCELLED";

                  return (
                    <tr key={b.booking_id}>
                      <td>
                        <code className="booking-code">{b.booking_id}</code>
                      </td>
                      <td><strong>Bay #{b.slot_id}</strong></td>
                      <td>{b.booking_date}</td>
                      <td>{b.start_time?.slice(0, 5)} - {b.end_time?.slice(0, 5)}</td>
                      <td>{formatDuration(b.duration_minutes)}</td>
                      <td>
                        {lifeStatus === "ACTIVE_NOW" && (
                          <span className="status-tag status-activenow">Active Now</span>
                        )}
                        {lifeStatus === "UPCOMING" && (
                          <span className="status-tag status-upcoming">Upcoming</span>
                        )}
                        {lifeStatus === "COMPLETED" && (
                          <span className="status-tag status-completed">Completed</span>
                        )}
                        {isCancelled && (
                          <span className="status-tag status-cancelled">Cancelled</span>
                        )}
                      </td>
                      <td>
                        {!isCancelled && lifeStatus !== "COMPLETED" ? (
                          <button
                            className="btn btn-table-cancel"
                            disabled={cancellingId === b.booking_id}
                            onClick={() => handleCancelBooking(b.booking_id, b.slot_id)}
                          >
                            {cancellingId === b.booking_id ? "..." : "Cancel"}
                          </button>
                        ) : isCancelled ? (
                          <span className="cancelled-label">Cancelled</span>
                        ) : (
                          <span className="cancelled-label">Completed</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
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
        <p>Smart Parking System • Time-Based Reservation Engine & ESP32 IoT Telemetry • CSE Major Project</p>
      </footer>
    </div>
  );
}

export default App;
