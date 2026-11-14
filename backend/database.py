import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone, date as dt_date
from typing import Optional, List, Dict, Any

try:
    from zoneinfo import ZoneInfo
    TIMEZONE = ZoneInfo("Asia/Kolkata")
except Exception:
    TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")

import pymysql
from pymysql.cursors import DictCursor

BUFFER_MINUTES = 1
# Default SENSOR_TIMEOUT_SECONDS to 0 (disabled) so idle sensors are not falsely marked as errors
SENSOR_TIMEOUT_SECONDS = int(os.getenv("SENSOR_TIMEOUT_SECONDS", "0"))

MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE")


def is_mysql_configured() -> bool:
    return bool(MYSQL_HOST and MYSQL_USER and MYSQL_DATABASE)


def get_current_time() -> datetime:
    """Returns the current timezone-aware datetime in Asia/Kolkata."""
    return datetime.now(TIMEZONE)


def format_time_value(val: Any) -> Optional[str]:
    """Formats time or timedelta into standard 2-digit zero-padded HH:MM:SS string."""
    if val is None:
        return None
    if isinstance(val, timedelta):
        total_seconds = int(val.total_seconds())
        h = (total_seconds // 3600) % 24
        m = (total_seconds % 3600) // 60
        s = total_seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    if isinstance(val, datetime):
        return val.strftime("%H:%M:%S")
    s = str(val).strip()
    if not s:
        return None
    parts = s.split(":")
    if len(parts) >= 2:
        try:
            h = int(parts[0])
            m = int(parts[1])
            sec = int(parts[2].split(".")[0]) if len(parts) > 2 else 0
            return f"{h:02d}:{m:02d}:{sec:02d}"
        except ValueError:
            pass
    return s


def format_date_value(val: Any) -> Optional[str]:
    """Formats date into standard YYYY-MM-DD string."""
    if val is None:
        return None
    if isinstance(val, (datetime, dt_date)):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    return s[:10] if len(s) >= 10 else s


def parse_datetime_to_kolkata(dt_val: Any) -> Optional[datetime]:
    """Safely converts string or datetime to Asia/Kolkata timezone-aware datetime."""
    if dt_val is None:
        return None
    if isinstance(dt_val, datetime):
        if dt_val.tzinfo is None:
            return dt_val.replace(tzinfo=TIMEZONE)
        return dt_val.astimezone(TIMEZONE)
    s = str(dt_val).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=TIMEZONE)
        except ValueError:
            continue
    return None


def check_slot_bookable_today(slot_bookings: list, target_date_str: str) -> bool:
    """
    Evaluates whether a parking slot remains available for at least some bookable time
    during the specified date (target_date_str in YYYY-MM-DD).
    
    A slot is considered bookable (True) if there is any free time window left today.
    It is considered fully booked (False) ONLY when booked for the entire day (e.g. 
    an all-day reservation of 1440 min or merged reservations covering [0, 1440]).
    
    Hourly or sequential bookings (e.g. 08:00 to 09:00) leave the rest of the day free,
    so the bay remains Available for Booking.
    """
    blocked_ranges = []
    for b in slot_bookings:
        if b.get("booking_status") == "CANCELLED":
            continue
        s_dt = parse_datetime_to_kolkata(b.get("start_datetime"))
        e_dt = parse_datetime_to_kolkata(b.get("end_datetime"))
        if not s_dt or not e_dt:
            continue

        s_date = s_dt.strftime("%Y-%m-%d")
        e_date = e_dt.strftime("%Y-%m-%d")

        # Check if booking touches target date
        if s_date != target_date_str and e_date != target_date_str:
            # Check if it spans across target date
            if s_date < target_date_str and e_date > target_date_str:
                return False
            continue

        s_min = (s_dt.hour * 60 + s_dt.minute) if s_date == target_date_str else 0
        e_min = (e_dt.hour * 60 + e_dt.minute) if e_date == target_date_str else 1440
        dur = int(b.get("duration_minutes") or (e_min - s_min))

        # Check if this single booking covers the entire day
        if dur >= 1439 or (s_min <= 0 and e_min >= 1439):
            return False

        blocked_ranges.append((max(0, s_min), min(1440, e_min)))

    if not blocked_ranges:
        return True

    # Merge overlapping blocked ranges
    blocked_ranges.sort()
    merged = []
    for start, end in blocked_ranges:
        if not merged or merged[-1][1] < start:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)

    # Check if merged ranges cover the entire 1440 minutes of the day
    total_blocked = sum(end - start for start, end in merged)
    if total_blocked >= 1439:
        return False

    return True


class DatabaseManager:
    """
    Unified database manager supporting production MySQL with resilient fallback
    to local SQLite for offline development and self-contained testing.
    """

    def __init__(self):
        self.use_mysql = is_mysql_configured()
        self.sqlite_path = os.path.join(os.path.dirname(__file__), "smartparking.db")

    def get_connection(self):
        if self.use_mysql:
            try:
                conn = pymysql.connect(
                    host=MYSQL_HOST,
                    port=MYSQL_PORT,
                    user=MYSQL_USER,
                    password=MYSQL_PASSWORD,
                    database=MYSQL_DATABASE,
                    cursorclass=DictCursor,
                    autocommit=False,
                    connect_timeout=5,
                )
                return conn, "mysql"
            except Exception as e:
                print(f"[DB WARNING] Failed to connect to MySQL ({e}). Falling back to SQLite.")
                self.use_mysql = False

        conn = sqlite3.connect(self.sqlite_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"

    def init_db(self):
        """Initializes tables and seeds initial 3 parking slots."""
        conn, db_type = self.get_connection()
        cursor = conn.cursor()
        now_str = get_current_time().strftime("%Y-%m-%d %H:%M:%S")

        try:
            if db_type == "mysql":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS parking_slots (
                        id INT PRIMARY KEY,
                        slot_number VARCHAR(16) NOT NULL,
                        is_active TINYINT(1) NOT NULL DEFAULT 1,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sensor_status (
                        slot_id INT PRIMARY KEY,
                        physical_status VARCHAR(20) NOT NULL DEFAULT 'Empty',
                        distance_cm FLOAT DEFAULT NULL,
                        last_heartbeat DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        is_healthy TINYINT(1) NOT NULL DEFAULT 1,
                        FOREIGN KEY (slot_id) REFERENCES parking_slots(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sensor_logs (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        slot_id INT NOT NULL,
                        status VARCHAR(20) NOT NULL,
                        distance_cm FLOAT DEFAULT NULL,
                        recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_slot_rec (slot_id, recorded_at),
                        FOREIGN KEY (slot_id) REFERENCES parking_slots(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS bookings (
                        booking_id VARCHAR(36) PRIMARY KEY,
                        slot_id INT NOT NULL,
                        booking_date DATE NOT NULL,
                        start_time TIME NOT NULL,
                        end_time TIME NOT NULL,
                        start_datetime DATETIME NOT NULL,
                        end_datetime DATETIME NOT NULL,
                        duration_minutes INT NOT NULL,
                        booking_status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX idx_slot_time (slot_id, booking_status, start_datetime, end_datetime),
                        FOREIGN KEY (slot_id) REFERENCES parking_slots(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)

                # Auto-migration: Drop any legacy UNIQUE constraints on bookings table
                # that would prevent multiple sequential bookings on the same slot_id
                try:
                    cursor.execute("""
                        SELECT DISTINCT INDEX_NAME
                        FROM information_schema.statistics
                        WHERE TABLE_SCHEMA = DATABASE()
                          AND TABLE_NAME = 'bookings'
                          AND NON_UNIQUE = 0
                          AND INDEX_NAME != 'PRIMARY';
                    """)
                    unique_indices = cursor.fetchall()
                    for u_idx in unique_indices:
                        idx_name = u_idx.get("INDEX_NAME") if isinstance(u_idx, dict) else u_idx[0]
                        if idx_name:
                            try:
                                cursor.execute(f"ALTER TABLE bookings DROP INDEX `{idx_name}`;")
                                print(f"[DB MIGRATION] Dropped unique index '{idx_name}' from bookings table.")
                            except Exception as drop_err:
                                print(f"[DB MIGRATION WARNING] Could not drop index {idx_name}: {drop_err}")
                except Exception as check_err:
                    print(f"[DB MIGRATION NOTICE] Index check notice: {check_err}")

                for slot_id in (1, 2, 3):
                    cursor.execute("""
                        INSERT INTO parking_slots (id, slot_number, is_active)
                        VALUES (%s, %s, 1)
                        ON DUPLICATE KEY UPDATE slot_number=VALUES(slot_number);
                    """, (slot_id, f"Slot {slot_id}"))
                    cursor.execute("""
                        INSERT INTO sensor_status (slot_id, physical_status, distance_cm, last_heartbeat, is_healthy)
                        VALUES (%s, 'Empty', 200.0, %s, 1)
                        ON DUPLICATE KEY UPDATE slot_id=slot_id;
                    """, (slot_id, now_str))
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS parking_slots (
                        id INTEGER PRIMARY KEY,
                        slot_number TEXT NOT NULL,
                        is_active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sensor_status (
                        slot_id INTEGER PRIMARY KEY,
                        physical_status TEXT NOT NULL DEFAULT 'Empty',
                        distance_cm REAL DEFAULT NULL,
                        last_heartbeat TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        is_healthy INTEGER NOT NULL DEFAULT 1,
                        FOREIGN KEY (slot_id) REFERENCES parking_slots(id)
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sensor_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        slot_id INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        distance_cm REAL DEFAULT NULL,
                        recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS bookings (
                        booking_id TEXT PRIMARY KEY,
                        slot_id INTEGER NOT NULL,
                        booking_date TEXT NOT NULL,
                        start_time TEXT NOT NULL,
                        end_time TEXT NOT NULL,
                        start_datetime TEXT NOT NULL,
                        end_datetime TEXT NOT NULL,
                        duration_minutes INTEGER NOT NULL,
                        booking_status TEXT NOT NULL DEFAULT 'ACTIVE',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (slot_id) REFERENCES parking_slots(id)
                    );
                """)

                for slot_id in (1, 2, 3):
                    cursor.execute("""
                        INSERT OR IGNORE INTO parking_slots (id, slot_number, is_active)
                        VALUES (?, ?, 1);
                    """, (slot_id, f"Slot {slot_id}"))
                    cursor.execute("""
                        INSERT OR IGNORE INTO sensor_status (slot_id, physical_status, distance_cm, last_heartbeat, is_healthy)
                        VALUES (?, 'Empty', 200.0, ?, 1);
                    """, (slot_id, now_str))

            conn.commit()
        finally:
            cursor.close()
            conn.close()

    def update_sensor_status(self, slot_id: int, status: str, distance_cm: float = None):
        """Records sensor update and logs measurement."""
        conn, db_type = self.get_connection()
        cursor = conn.cursor()
        now_str = get_current_time().strftime("%Y-%m-%d %H:%M:%S")

        is_healthy = 1 if status in ("Empty", "Occupied") else 0

        try:
            if db_type == "mysql":
                cursor.execute("""
                    UPDATE sensor_status
                    SET physical_status = %s, distance_cm = %s, last_heartbeat = %s, is_healthy = %s
                    WHERE slot_id = %s;
                """, (status, distance_cm, now_str, is_healthy, slot_id))
                cursor.execute("""
                    INSERT INTO sensor_logs (slot_id, status, distance_cm, recorded_at)
                    VALUES (%s, %s, %s, %s);
                """, (slot_id, status, distance_cm, now_str))
            else:
                cursor.execute("""
                    UPDATE sensor_status
                    SET physical_status = ?, distance_cm = ?, last_heartbeat = ?, is_healthy = ?
                    WHERE slot_id = ?;
                """, (status, distance_cm, now_str, is_healthy, slot_id))
                cursor.execute("""
                    INSERT INTO sensor_logs (slot_id, status, distance_cm, recorded_at)
                    VALUES (?, ?, ?, ?);
                """, (slot_id, status, distance_cm, now_str))

            conn.commit()
            return True
        finally:
            cursor.close()
            conn.close()

    def get_all_sensor_status(self):
        """Returns hardware sensor status for all 3 slots."""
        conn, _ = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT slot_id as id, physical_status as status, distance_cm, last_heartbeat, is_healthy
                FROM sensor_status
                ORDER BY slot_id ASC;
            """)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            cursor.close()
            conn.close()

    def create_booking(self, slot_id: int, date_str: str, start_time_str: str, duration_minutes: int):
        """
        Creates a new reservation with safe concurrency, past-time checks,
        and strict 1-minute buffer conflict detection.
        Supports unlimited sequential valid bookings on the same slot.
        """
        if slot_id not in (1, 2, 3):
            raise ValueError(f"Invalid parking slot {slot_id}. Must be 1, 2, or 3.")

        if duration_minutes < 1 or duration_minutes > 1440:
            raise ValueError("Duration must be between 1 and 1440 minutes.")

        # Parse date and start time in Asia/Kolkata
        try:
            start_dt = datetime.strptime(f"{date_str} {start_time_str[:5]}", "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)
        except ValueError:
            raise ValueError("Invalid date or time format. Expected YYYY-MM-DD and HH:MM.")

        now_kolkata = get_current_time()
        # Allow bookings for today and future dates
        if start_dt.date() < now_kolkata.date():
            raise ValueError(f"Cannot book a past date. Selected date is {date_str}, current date in Asia/Kolkata is {now_kolkata.strftime('%Y-%m-%d')}.")

        end_dt = start_dt + timedelta(minutes=duration_minutes)

        conn, db_type = self.get_connection()
        cursor = conn.cursor()

        try:
            # Check existing ACTIVE bookings for this slot (with transaction lock on MySQL)
            if db_type == "mysql":
                cursor.execute("""
                    SELECT booking_id, booking_date, start_time, end_time, start_datetime, end_datetime
                    FROM bookings
                    WHERE slot_id = %s AND booking_status = 'ACTIVE'
                    FOR UPDATE;
                """, (slot_id,))
            else:
                cursor.execute("""
                    SELECT booking_id, booking_date, start_time, end_time, start_datetime, end_datetime
                    FROM bookings
                    WHERE slot_id = ? AND booking_status = 'ACTIVE';
                """, (slot_id,))

            existing_bookings = cursor.fetchall()
            buffer_td = timedelta(minutes=BUFFER_MINUTES)

            for b in existing_bookings:
                b_dict = dict(b)
                ex_start = parse_datetime_to_kolkata(b_dict["start_datetime"])
                ex_end = parse_datetime_to_kolkata(b_dict["end_datetime"])

                if not ex_start or not ex_end:
                    continue

                buffered_ex_start = ex_start - buffer_td
                buffered_ex_end = ex_end + buffer_td

                # Conflict occurs if [start_dt, end_dt] overlaps [buffered_ex_start, buffered_ex_end]
                # Overlap condition: start_dt < buffered_ex_end and end_dt > buffered_ex_start
                if start_dt < buffered_ex_end and end_dt > buffered_ex_start:
                    raise ValueError(
                        f"Slot {slot_id} is unavailable during this time. "
                        f"Existing reservation: {ex_start.strftime('%Y-%m-%d %H:%M')} - "
                        f"{ex_end.strftime('%H:%M')} (plus 1-min safety buffer). "
                        "Please choose another slot or non-overlapping time."
                    )

            booking_uuid = f"BK-{uuid.uuid4().hex[:8].upper()}"
            start_dt_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
            end_dt_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
            date_part = start_dt.strftime("%Y-%m-%d")
            start_time_part = start_dt.strftime("%H:%M:00")
            end_time_part = end_dt.strftime("%H:%M:00")
            now_str = now_kolkata.strftime("%Y-%m-%d %H:%M:%S")

            if db_type == "mysql":
                cursor.execute("""
                    INSERT INTO bookings (
                        booking_id, slot_id, booking_date, start_time, end_time,
                        start_datetime, end_datetime, duration_minutes, booking_status,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'ACTIVE', %s, %s);
                """, (
                    booking_uuid, slot_id, date_part, start_time_part, end_time_part,
                    start_dt_str, end_dt_str, duration_minutes, now_str, now_str
                ))
            else:
                cursor.execute("""
                    INSERT INTO bookings (
                        booking_id, slot_id, booking_date, start_time, end_time,
                        start_datetime, end_datetime, duration_minutes, booking_status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?);
                """, (
                    booking_uuid, slot_id, date_part, start_time_part, end_time_part,
                    start_dt_str, end_dt_str, duration_minutes, now_str, now_str
                ))

            conn.commit()

            return {
                "booking_id": booking_uuid,
                "slot_id": slot_id,
                "date": date_part,
                "start_time": start_dt.strftime("%H:%M"),
                "end_time": end_dt.strftime("%H:%M"),
                "duration_minutes": duration_minutes,
                "booking_status": "ACTIVE",
                "created_at": now_str,
            }
        finally:
            cursor.close()
            conn.close()

    def cancel_booking(self, booking_id: str):
        """Cancels a booking by its unique booking ID. Preserves history in database."""
        conn, db_type = self.get_connection()
        cursor = conn.cursor()
        now_str = get_current_time().strftime("%Y-%m-%d %H:%M:%S")

        try:
            param_ph = "%s" if db_type == "mysql" else "?"
            cursor.execute(f"""
                SELECT booking_id, slot_id, booking_status, booking_date, start_time, end_time, duration_minutes
                FROM bookings
                WHERE booking_id = {param_ph};
            """, (booking_id,))

            row = cursor.fetchone()
            if not row:
                return None

            b_dict = dict(row)
            if b_dict["booking_status"] == "CANCELLED":
                return b_dict

            cursor.execute(f"""
                UPDATE bookings
                SET booking_status = 'CANCELLED', updated_at = {param_ph}
                WHERE booking_id = {param_ph};
            """, (now_str, booking_id))

            conn.commit()
            b_dict["booking_status"] = "CANCELLED"
            b_dict["start_time"] = format_time_value(b_dict.get("start_time"))
            b_dict["end_time"] = format_time_value(b_dict.get("end_time"))
            b_dict["booking_date"] = format_date_value(b_dict.get("booking_date"))
            return b_dict
        finally:
            cursor.close()
            conn.close()

    def cancel_slot_booking(self, slot_id: int, date: str = None, time: str = None, booking_id: str = None):
        """Cancels a booking for a specific slot by booking_id or latest/matching active booking."""
        conn, db_type = self.get_connection()
        cursor = conn.cursor()
        now_str = get_current_time().strftime("%Y-%m-%d %H:%M:%S")

        try:
            if booking_id:
                return self.cancel_booking(booking_id)

            param_ph = "%s" if db_type == "mysql" else "?"
            query = f"SELECT booking_id, slot_id, booking_status FROM bookings WHERE slot_id = {param_ph} AND booking_status = 'ACTIVE'"
            params = [slot_id]

            if date:
                query += f" AND booking_date = {param_ph}"
                params.append(date)
            if time:
                time_like = f"{time[:5]}%"
                query += f" AND start_time LIKE {param_ph}"
                params.append(time_like)

            query += " ORDER BY start_datetime ASC LIMIT 1;"

            cursor.execute(query, tuple(params))
            row = cursor.fetchone()
            if not row:
                return None

            b_dict = dict(row)
            target_id = b_dict["booking_id"]

            cursor.execute(f"""
                UPDATE bookings
                SET booking_status = 'CANCELLED', updated_at = {param_ph}
                WHERE booking_id = {param_ph};
            """, (now_str, target_id))

            conn.commit()
            b_dict["booking_status"] = "CANCELLED"
            return b_dict
        finally:
            cursor.close()
            conn.close()

    def get_bookings(self, slot_id: int = None, status: str = None):
        """Retrieves bookings ordered by start_datetime with clean serialized date/time fields."""
        conn, db_type = self.get_connection()
        cursor = conn.cursor()

        try:
            query = "SELECT * FROM bookings WHERE 1=1"
            params = []
            param_ph = "%s" if db_type == "mysql" else "?"

            if slot_id is not None and isinstance(slot_id, int):
                query += f" AND slot_id = {param_ph}"
                params.append(slot_id)
            if status is not None and isinstance(status, str):
                query += f" AND booking_status = {param_ph}"
                params.append(status)

            query += " ORDER BY start_datetime DESC;"
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

            results = []
            for r in rows:
                item = dict(r)
                item["start_time"] = format_time_value(item.get("start_time"))
                item["end_time"] = format_time_value(item.get("end_time"))
                item["booking_date"] = format_date_value(item.get("booking_date"))
                for k in ("start_datetime", "end_datetime", "created_at", "updated_at"):
                    if item.get(k) is not None:
                        item[k] = str(item[k])
                results.append(item)
            return results
        finally:
            cursor.close()
            conn.close()

    def get_booking_by_id(self, booking_id: str):
        conn, db_type = self.get_connection()
        cursor = conn.cursor()
        try:
            param_ph = "%s" if db_type == "mysql" else "?"
            cursor.execute(f"SELECT * FROM bookings WHERE booking_id = {param_ph};", (booking_id,))
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            item["start_time"] = format_time_value(item.get("start_time"))
            item["end_time"] = format_time_value(item.get("end_time"))
            item["booking_date"] = format_date_value(item.get("booking_date"))
            for k in ("start_datetime", "end_datetime", "created_at", "updated_at"):
                if item.get(k) is not None:
                    item[k] = str(item[k])
            return item
        finally:
            cursor.close()
            conn.close()

    def get_slot_availability(self, slot_id: int, target_date_str: Optional[str] = None) -> Dict[str, Any]:
        """
        Calculates booked intervals, available time windows, and the next available
        time for a slot on a given date (enforcing 1-minute safety buffers).
        Correctly handles midnight transitions.
        """
        now = get_current_time()
        today_str = now.strftime("%Y-%m-%d")
        date_str = target_date_str or today_str

        all_bookings = self.get_bookings(slot_id=slot_id, status="ACTIVE")

        # Filter bookings that overlap the target date
        intervals = []
        blocked_ranges = []

        for b in all_bookings:
            s_dt = parse_datetime_to_kolkata(b.get("start_datetime"))
            e_dt = parse_datetime_to_kolkata(b.get("end_datetime"))

            if not s_dt or not e_dt:
                continue

            s_date = s_dt.strftime("%Y-%m-%d")
            e_date = e_dt.strftime("%Y-%m-%d")

            # Check if this booking overlaps the target date
            if s_date != date_str and e_date != date_str:
                continue

            # Calculate minutes within target date
            if s_date == date_str:
                s_min = s_dt.hour * 60 + s_dt.minute
                s_raw = f"{s_dt.hour:02d}:{s_dt.minute:02d}"
            else:
                s_min = 0 # Started previous day
                s_raw = "00:00"

            if e_date == date_str:
                e_min = e_dt.hour * 60 + e_dt.minute
                e_raw = f"{e_dt.hour:02d}:{e_dt.minute:02d}"
            else:
                e_min = 1440 # Continues into next day
                e_raw = "23:59"

            duration_val = int(b.get("duration_minutes") or (e_min - s_min if e_min > s_min else 60))

            intervals.append({
                "booking_id": b["booking_id"],
                "date": date_str,
                "start_time": s_raw,
                "end_time": e_raw,
                "duration_minutes": duration_val,
            })
            # Add 1-minute buffer to blocked interval
            blocked_ranges.append((max(0, s_min - BUFFER_MINUTES), min(1440, e_min + BUFFER_MINUTES)))

        # Merge overlapping blocked ranges
        blocked_ranges.sort()
        merged_blocks = []
        for start, end in blocked_ranges:
            if not merged_blocks or merged_blocks[-1][1] < start:
                merged_blocks.append([start, end])
            else:
                merged_blocks[-1][1] = max(merged_blocks[-1][1], end)

        # Compute free windows
        available_windows = []
        cur_min = 0
        for b_start, b_end in merged_blocks:
            if b_start > cur_min:
                start_h = cur_min // 60
                start_m = cur_min % 60
                end_h = b_start // 60
                end_m = b_start % 60
                available_windows.append({
                    "start_time": f"{start_h:02d}:{start_m:02d}",
                    "end_time": f"{end_h:02d}:{end_m:02d}",
                    "duration_minutes": b_start - cur_min,
                })
            cur_min = max(cur_min, b_end)

        if cur_min < 1440:
            start_h = cur_min // 60
            start_m = cur_min % 60
            available_windows.append({
                "start_time": f"{start_h:02d}:{start_m:02d}",
                "end_time": "23:59",
                "duration_minutes": 1440 - cur_min,
            })

        # Calculate next available time
        next_available_time = "Available Now"
        if date_str == today_str:
            now_minute = now.hour * 60 + now.minute
            inside_block = None
            for b_start, b_end in merged_blocks:
                if b_start <= now_minute < b_end:
                    inside_block = b_end
                    break

            if inside_block is not None:
                if inside_block < 1440:
                    next_h = inside_block // 60
                    next_m = inside_block % 60
                    next_available_time = f"{next_h:02d}:{next_m:02d}"
                else:
                    next_available_time = "Fully booked for today"
            else:
                next_available_time = "Available Now"
        else:
            if available_windows:
                next_available_time = available_windows[0]["start_time"]
            else:
                next_available_time = "Fully booked for this date"

        return {
            "slot_id": slot_id,
            "date": date_str,
            "booked_intervals": intervals,
            "available_windows": available_windows,
            "next_available_time": next_available_time,
        }

    def get_slots_with_status(self):
        """
        Computes accurate slot states and dashboard metrics:
        - Physically Occupied: Sensor detects car right now
        - Physical Available: Total Slots - Physically Occupied (Empty bays)
        - Reserved: Active reservation covering current time, bay empty
        - Upcoming Reservation: Future reservation exists, bay empty now
        - Sensor Error: Sensor state is 'Error'
        - Available Now / Ready to Book: Neither physically occupied, nor reserved right now, nor sensor error
        - Booked / Reserved (Section 2 KPI): Total count of active + upcoming confirmed bookings that end after now!
        """
        now = get_current_time()
        conn, _ = self.get_connection()
        cursor = conn.cursor()

        try:
            # 1. Fetch slots and sensor status
            cursor.execute("""
                SELECT p.id, p.slot_number, s.physical_status, s.distance_cm, s.last_heartbeat, s.is_healthy
                FROM parking_slots p
                LEFT JOIN sensor_status s ON p.id = s.slot_id
                ORDER BY p.id ASC;
            """)
            slots_rows = [dict(r) for r in cursor.fetchall()]

            # 2. Fetch all ACTIVE bookings for evaluation
            cursor.execute("""
                SELECT booking_id, slot_id, booking_date, start_time, end_time,
                       start_datetime, end_datetime, duration_minutes, booking_status
                FROM bookings
                WHERE booking_status = 'ACTIVE'
                ORDER BY start_datetime ASC;
            """)
            raw_bookings_rows = [dict(r) for r in cursor.fetchall()]

            bookings_rows = []
            for b in raw_bookings_rows:
                b_item = dict(b)
                b_item["start_time"] = format_time_value(b_item.get("start_time"))
                b_item["end_time"] = format_time_value(b_item.get("end_time"))
                b_item["booking_date"] = format_date_value(b_item.get("booking_date"))
                for k in ("start_datetime", "end_datetime"):
                    if b_item.get(k) is not None:
                        b_item[k] = str(b_item[k])
                bookings_rows.append(b_item)

            bookings_by_slot = {1: [], 2: [], 3: []}
            for b in bookings_rows:
                s_id = b["slot_id"]
                if s_id in bookings_by_slot:
                    bookings_by_slot[s_id].append(b)

            computed_slots = []
            for s in slots_rows:
                slot_id = s["id"]
                phys_status = s.get("physical_status") or "Empty"
                dist = s.get("distance_cm")
                heartbeat = str(s.get("last_heartbeat") or "")
                is_healthy_flag = bool(s.get("is_healthy", 1))

                # Check optional heartbeat timeout only if explicitly configured (> 0)
                hb_dt = parse_datetime_to_kolkata(s.get("last_heartbeat"))
                is_timed_out = False
                if hb_dt and SENSOR_TIMEOUT_SECONDS > 0:
                    elapsed = (now - hb_dt).total_seconds()
                    if elapsed > SENSOR_TIMEOUT_SECONDS:
                        is_timed_out = True

                is_sensor_error = (phys_status in ("Error", "Unknown") or not is_healthy_flag or is_timed_out)
                is_physically_occupied = (phys_status == "Occupied" and not is_sensor_error)

                slot_b_list = bookings_by_slot.get(slot_id, [])

                # Check bookings for this slot
                active_booking = None
                next_booking = None
                upcoming_bookings_list = []
                booked_intervals = []

                for b in slot_b_list:
                    s_dt = parse_datetime_to_kolkata(b["start_datetime"])
                    e_dt = parse_datetime_to_kolkata(b["end_datetime"])

                    if not s_dt or not e_dt:
                        continue

                    b_interval = {
                        "booking_id": b["booking_id"],
                        "date": str(b["booking_date"]),
                        "start_time": str(b["start_time"])[:5],
                        "end_time": str(b["end_time"])[:5],
                        "duration_minutes": b.get("duration_minutes", 60),
                    }
                    booked_intervals.append(b_interval)

                    # Active booking: CURRENT TIME falls strictly within booking window
                    if s_dt <= now < e_dt:
                        active_booking = b
                    elif s_dt > now:
                        upcoming_bookings_list.append(b)
                        if next_booking is None:
                            next_booking = b

                is_currently_reserved = (active_booking is not None)
                has_upcoming_reservation = (next_booking is not None)

                # Available for Booking (Day-Level):
                # Bay remains available for booking today if at least some bookable time remains today.
                # Only decreases when the bay is booked for the entire day (1440 min).
                today_str = now.strftime("%Y-%m-%d")
                is_bookable_today = check_slot_bookable_today(slot_b_list, today_str)

                # Available right now: Physically empty, no active reservation right now, and no sensor error!
                is_available_now = (not is_physically_occupied and not is_currently_reserved and not is_sensor_error)

                # Effective Display Status:
                # 1. Occupied: Physical vehicle present (highest priority)
                # 2. Sensor Error: Hardware error or timeout
                # 3. Reserved (all-day): Fully booked for today
                # 4. Reserved: Active reservation right now and empty
                # 5. Upcoming Reservation: Has future booking, empty now
                # 6. Available: Clear, empty, ready for booking
                if is_physically_occupied:
                    effective_status = "Occupied"
                elif is_sensor_error:
                    effective_status = "Sensor Error"
                elif not is_bookable_today:
                    effective_status = "Reserved"
                elif is_currently_reserved:
                    effective_status = "Reserved"
                elif has_upcoming_reservation:
                    effective_status = "Upcoming Reservation"
                else:
                    effective_status = "Available"

                # Pick latest active or upcoming booking for display
                display_booking = active_booking or next_booking or (slot_b_list[-1] if slot_b_list else None)

                # Formulate human-readable availability caption
                if is_sensor_error:
                    availability_window = "Sensor Error / Disconnected"
                elif is_physically_occupied:
                    if is_currently_reserved:
                        availability_window = f"Occupied (Reserved until {str(active_booking['end_time'])[:5]})"
                    else:
                        availability_window = "Occupied by vehicle"
                elif not is_bookable_today:
                    availability_window = "Fully booked for today"
                elif is_currently_reserved:
                    availability_window = f"Reserved until {str(active_booking['end_time'])[:5]}"
                elif has_upcoming_reservation:
                    availability_window = f"Available now until {str(next_booking['start_time'])[:5]}"
                else:
                    availability_window = "Available for booking"

                computed_slots.append({
                    "id": slot_id,
                    "slot_number": s["slot_number"],
                    "status": effective_status,
                    "physical_status": "Error" if is_sensor_error else phys_status,
                    "is_physically_occupied": is_physically_occupied,
                    "is_currently_reserved": is_currently_reserved,
                    "has_upcoming_reservation": has_upcoming_reservation,
                    "is_bookable_today": is_bookable_today,
                    "is_available_for_booking": is_bookable_today,
                    "is_available_now": is_available_now,
                    "reservation_status": "Active Reservation" if is_currently_reserved else ("Upcoming Reservation" if has_upcoming_reservation else "None"),
                    "distance_cm": dist if not is_sensor_error else None,
                    "last_sensor_update": heartbeat,
                    "is_sensor_healthy": not is_sensor_error,
                    "availability_window": availability_window,
                    "booked_intervals": booked_intervals,
                    "active_booking": {
                        "booking_id": active_booking["booking_id"],
                        "date": str(active_booking["booking_date"]),
                        "start_time": str(active_booking["start_time"])[:5],
                        "end_time": str(active_booking["end_time"])[:5],
                        "duration_minutes": active_booking["duration_minutes"],
                    } if active_booking else None,
                    "next_booking": {
                        "booking_id": next_booking["booking_id"],
                        "date": str(next_booking["booking_date"]),
                        "start_time": str(next_booking["start_time"])[:5],
                        "end_time": str(next_booking["end_time"])[:5],
                        "duration_minutes": next_booking.get("duration_minutes", 60),
                    } if next_booking else None,
                    "upcoming_count": len(upcoming_bookings_list),
                    "all_upcoming_bookings": [
                        {
                            "booking_id": ub["booking_id"],
                            "date": str(ub["booking_date"]),
                            "start_time": str(ub["start_time"])[:5],
                            "end_time": str(ub["end_time"])[:5],
                            "duration_minutes": ub.get("duration_minutes", 60),
                        }
                        for ub in upcoming_bookings_list
                    ],
                    # Backward compatibility fields
                    "date": str(display_booking["booking_date"]) if display_booking else None,
                    "start_time": str(display_booking["start_time"])[:5] if display_booking else None,
                    "end_time": str(display_booking["end_time"])[:5] if display_booking else None,
                    "duration_minutes": display_booking["duration_minutes"] if display_booking else None,
                    "booking_id": display_booking["booking_id"] if display_booking else None,
                })

            # Calculate accurate dashboard summary counts:
            total_slots = len(computed_slots)
            physically_occupied = sum(1 for s in computed_slots if s["is_physically_occupied"])
            physical_available = total_slots - physically_occupied
            available_for_booking = sum(1 for s in computed_slots if s["is_bookable_today"])
            sensor_errors = sum(1 for s in computed_slots if s["status"] == "Sensor Error")
            active_reservations = sum(1 for s in computed_slots if s["is_currently_reserved"])
            upcoming_reservations = sum(s["upcoming_count"] for s in computed_slots)

            # Section 2 Definition of BOOKED / RESERVED KPI:
            # Number of confirmed, non-cancelled reservation records that end in the future (end_datetime > now).
            # Includes BOTH active reservations and upcoming reservations.
            # Multiple sequential bookings on the same bay are counted separately.
            active_or_upcoming_bookings = [
                b for b in bookings_rows
                if parse_datetime_to_kolkata(b.get("end_datetime")) and parse_datetime_to_kolkata(b.get("end_datetime")) > now
            ]
            booked_reserved = len(active_or_upcoming_bookings)

            counts = {
                "total_slots": total_slots,
                "physical_available": physical_available,
                "physically_occupied": physically_occupied,
                "booked_reserved": booked_reserved,
                "available_for_booking": available_for_booking,
                "available_now": available_for_booking,
                "sensor_errors": sensor_errors,
                "active_reservations": active_reservations,
                "upcoming_reservations": upcoming_reservations,
                # Backward compatibility aliases:
                "total": total_slots,
                "available": physical_available,
                "physical_occupied": physically_occupied,
                "occupied": physically_occupied,
                "reserved": booked_reserved,
            }

            return {
                "slots": computed_slots,
                "summary": counts,
                "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "timezone": "Asia/Kolkata",
            }
        finally:
            cursor.close()
            conn.close()


# Singleton database instance
db = DatabaseManager()
