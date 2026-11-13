import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    TIMEZONE = ZoneInfo("Asia/Kolkata")
except Exception:
    TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")

import pymysql
from pymysql.cursors import DictCursor
BUFFER_MINUTES = 1

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
                # If MySQL fails to connect, fallback to SQLite and log warning
                print(f"[DB WARNING] Failed to connect to MySQL ({e}). Falling back to SQLite.")
                self.use_mysql = False

        conn = sqlite3.connect(self.sqlite_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"

    def init_db(self):
        """Initializes tables and seeds initial 3 parking slots."""
        conn, db_type = self.get_connection()
        cursor = conn.cursor()

        try:
            if db_type == "mysql":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS parking_slots (
                        id INT PRIMARY KEY,
                        slot_number VARCHAR(16) NOT NULL,
                        is_active TINYINT(1) NOT NULL DEFAULT 1,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sensor_status (
                        slot_id INT PRIMARY KEY,
                        physical_status VARCHAR(20) NOT NULL DEFAULT 'Empty',
                        distance_cm FLOAT DEFAULT NULL,
                        last_heartbeat DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        is_healthy TINYINT(1) NOT NULL DEFAULT 1,
                        FOREIGN KEY (slot_id) REFERENCES parking_slots(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sensor_logs (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        slot_id INT NOT NULL,
                        status VARCHAR(20) NOT NULL,
                        distance_cm FLOAT DEFAULT NULL,
                        recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_slot_rec (slot_id, recorded_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
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
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """)

                for slot_id in (1, 2, 3):
                    cursor.execute("""
                        INSERT INTO parking_slots (id, slot_number, is_active)
                        VALUES (%s, %s, 1)
                        ON DUPLICATE KEY UPDATE slot_number=VALUES(slot_number);
                    """, (slot_id, f"Slot {slot_id}"))
                    cursor.execute("""
                        INSERT INTO sensor_status (slot_id, physical_status, distance_cm, last_heartbeat, is_healthy)
                        VALUES (%s, 'Empty', 200.0, NOW(), 1)
                        ON DUPLICATE KEY UPDATE physical_status=physical_status;
                    """, (slot_id,))
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
                        VALUES (?, 'Empty', 200.0, datetime('now'), 1);
                    """, (slot_id,))

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
        """
        # Validate slot existence
        if slot_id not in (1, 2, 3):
            raise ValueError(f"Invalid parking slot {slot_id}. Must be 1, 2, or 3.")

        if duration_minutes < 1 or duration_minutes > 1440:
            raise ValueError("Duration must be between 1 and 1440 minutes.")

        # Parse date and start time in Asia/Kolkata
        try:
            start_dt = datetime.strptime(f"{date_str} {start_time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)
        except ValueError:
            raise ValueError("Invalid date or time format. Expected YYYY-MM-DD and HH:MM.")

        now_kolkata = get_current_time()
        # Allow a small 60-second leeway for clock skew if user selects right now
        if start_dt < (now_kolkata - timedelta(seconds=60)):
            raise ValueError(f"Cannot book a time in the past. Current time in Asia/Kolkata is {now_kolkata.strftime('%Y-%m-%d %H:%M')}.")

        end_dt = start_dt + timedelta(minutes=duration_minutes)

        conn, db_type = self.get_connection()
        cursor = conn.cursor()

        try:
            # Check physical sensor if booking starts immediately
            if start_dt <= (now_kolkata + timedelta(minutes=1)):
                if db_type == "mysql":
                    cursor.execute("SELECT physical_status FROM sensor_status WHERE slot_id = %s FOR UPDATE;", (slot_id,))
                else:
                    cursor.execute("SELECT physical_status FROM sensor_status WHERE slot_id = ?;", (slot_id,))
                sensor_row = cursor.fetchone()
                if sensor_row and dict(sensor_row).get("physical_status") == "Occupied":
                    raise ValueError(f"Slot {slot_id} is currently physically occupied by a vehicle.")

            # Check existing ACTIVE bookings for this slot
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
                # Parse existing start and end datetimes
                s_raw = b_dict["start_datetime"]
                e_raw = b_dict["end_datetime"]

                if isinstance(s_raw, str):
                    ex_start = datetime.strptime(s_raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TIMEZONE)
                    ex_end = datetime.strptime(e_raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TIMEZONE)
                else:
                    ex_start = s_raw.replace(tzinfo=TIMEZONE)
                    ex_end = e_raw.replace(tzinfo=TIMEZONE)

                buffered_ex_start = ex_start - buffer_td
                buffered_ex_end = ex_end + buffer_td

                # Conflict occurs if [start_dt, end_dt] overlaps [buffered_ex_start, buffered_ex_end]
                if start_dt < buffered_ex_end and end_dt > buffered_ex_start:
                    raise ValueError(
                        f"Slot {slot_id} is unavailable during this time. "
                        f"Existing reservation: {ex_start.strftime('%Y-%m-%d %H:%M')} - "
                        f"{ex_end.strftime('%H:%M')} (plus 1-min buffer). "
                        "Please choose another slot or time."
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
        """Cancels a booking by its unique booking ID."""
        conn, db_type = self.get_connection()
        cursor = conn.cursor()
        now_str = get_current_time().strftime("%Y-%m-%d %H:%M:%S")

        try:
            if db_type == "mysql":
                cursor.execute("""
                    SELECT booking_id, slot_id, booking_status
                    FROM bookings
                    WHERE booking_id = %s;
                """, (booking_id,))
            else:
                cursor.execute("""
                    SELECT booking_id, slot_id, booking_status
                    FROM bookings
                    WHERE booking_id = ?;
                """, (booking_id,))

            row = cursor.fetchone()
            if not row:
                return None

            b_dict = dict(row)
            if b_dict["booking_status"] == "CANCELLED":
                return b_dict

            if db_type == "mysql":
                cursor.execute("""
                    UPDATE bookings
                    SET booking_status = 'CANCELLED', updated_at = %s
                    WHERE booking_id = %s;
                """, (now_str, booking_id))
            else:
                cursor.execute("""
                    UPDATE bookings
                    SET booking_status = 'CANCELLED', updated_at = ?
                    WHERE booking_id = ?;
                """, (now_str, booking_id))

            conn.commit()
            b_dict["booking_status"] = "CANCELLED"
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

            # Look up active booking by date and start_time
            query = """
                SELECT booking_id, slot_id, booking_status
                FROM bookings
                WHERE slot_id = {param} AND booking_status = 'ACTIVE'
            """.format(param="%s" if db_type == "mysql" else "?")
            params = [slot_id]

            if date:
                query += " AND booking_date = {param}".format(param="%s" if db_type == "mysql" else "?")
                params.append(date)
            if time:
                # support HH:MM or HH:MM:SS
                time_like = f"{time[:5]}%"
                query += " AND start_time LIKE {param}".format(param="%s" if db_type == "mysql" else "?")
                params.append(time_like)

            query += " ORDER BY start_datetime ASC LIMIT 1;"

            cursor.execute(query, tuple(params))
            row = cursor.fetchone()
            if not row:
                return None

            b_dict = dict(row)
            target_id = b_dict["booking_id"]

            if db_type == "mysql":
                cursor.execute("""
                    UPDATE bookings
                    SET booking_status = 'CANCELLED', updated_at = %s
                    WHERE booking_id = %s;
                """, (now_str, target_id))
            else:
                cursor.execute("""
                    UPDATE bookings
                    SET booking_status = 'CANCELLED', updated_at = ?
                    WHERE booking_id = ?;
                """, (now_str, target_id))

            conn.commit()
            b_dict["booking_status"] = "CANCELLED"
            return b_dict
        finally:
            cursor.close()
            conn.close()

    def get_bookings(self, slot_id: int = None, status: str = None):
        """Retrieves bookings ordered by start_datetime."""
        conn, db_type = self.get_connection()
        cursor = conn.cursor()

        try:
            query = "SELECT * FROM bookings WHERE 1=1"
            params = []
            param_ph = "%s" if db_type == "mysql" else "?"

            if slot_id:
                query += f" AND slot_id = {param_ph}"
                params.append(slot_id)
            if status:
                query += f" AND booking_status = {param_ph}"
                params.append(status)

            query += " ORDER BY start_datetime DESC;"
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

            results = []
            for r in rows:
                item = dict(r)
                # Normalize time/datetime fields for JSON serialization
                for k in ("start_time", "end_time", "start_datetime", "end_datetime", "booking_date", "created_at", "updated_at"):
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
            for k in ("start_time", "end_time", "start_datetime", "end_datetime", "booking_date", "created_at", "updated_at"):
                if item.get(k) is not None:
                    item[k] = str(item[k])
            return item
        finally:
            cursor.close()
            conn.close()

    def get_slots_with_status(self):
        """
        Computes accurate slot states and dashboard metrics:
        - Physically Occupied: Sensor detects car right now
        - Booked/Reserved: Active reservation right now
        - Sensor Error: Sensor state is 'Error' or missing
        - Available: Neither occupied nor reserved nor in sensor error
        """
        now = get_current_time()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        conn, db_type = self.get_connection()
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
            bookings_rows = [dict(r) for r in cursor.fetchall()]

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

                slot_b_list = bookings_by_slot.get(slot_id, [])

                # Determine if there is an active reservation covering current time
                active_booking = None
                next_booking = None

                for b in slot_b_list:
                    s_raw = b["start_datetime"]
                    e_raw = b["end_datetime"]
                    if isinstance(s_raw, str):
                        s_dt = datetime.strptime(s_raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TIMEZONE)
                        e_dt = datetime.strptime(e_raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TIMEZONE)
                    else:
                        s_dt = s_raw.replace(tzinfo=TIMEZONE)
                        e_dt = e_raw.replace(tzinfo=TIMEZONE)

                    if s_dt <= now < e_dt:
                        active_booking = b
                    elif s_dt > now and next_booking is None:
                        next_booking = b

                # Compute effective slot status:
                # Priority:
                # 1. Physically Occupied: Sensor detects vehicle -> 'Occupied'
                # 2. Reservation: Has active reservation now or confirmed upcoming reservation -> 'Reserved'
                # 3. Sensor Error: Sensor hardware error or unknown -> 'Sensor Error'
                # 4. Otherwise -> 'Available'
                has_reservation = (active_booking is not None) or (next_booking is not None)

                if phys_status == "Occupied":
                    effective_status = "Occupied"
                elif has_reservation:
                    effective_status = "Reserved"
                elif phys_status in ("Error", "Unknown"):
                    effective_status = "Sensor Error"
                else:
                    effective_status = "Available"

                # Pick active or upcoming or latest booking for display
                display_booking = active_booking or next_booking or (slot_b_list[-1] if slot_b_list else None)

                computed_slots.append({
                    "id": slot_id,
                    "slot_number": s["slot_number"],
                    "status": effective_status,
                    "physical_status": phys_status,
                    "reservation_status": "Active Reservation" if active_booking else ("Upcoming Reservation" if next_booking else "None"),
                    "distance_cm": dist,
                    "last_sensor_update": heartbeat,
                    "is_sensor_healthy": bool(s.get("is_healthy", 1)),
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
                    # Backward compatibility fields for existing frontend expectations
                    "date": str(display_booking["booking_date"]) if display_booking else None,
                    "start_time": str(display_booking["start_time"])[:5] if display_booking else None,
                    "end_time": str(display_booking["end_time"])[:5] if display_booking else None,
                    "duration_minutes": display_booking["duration_minutes"] if display_booking else None,
                    "booking_id": display_booking["booking_id"] if display_booking else None,
                })

            # Calculate accurate dashboard summary counts
            total_slots = len(computed_slots)
            physically_occupied = sum(1 for s in computed_slots if s["physical_status"] == "Occupied")
            booked_reserved = sum(1 for s in computed_slots if s["status"] == "Reserved")
            sensor_errors = sum(1 for s in computed_slots if s["physical_status"] in ("Error", "Unknown"))
            available_now = sum(1 for s in computed_slots if s["status"] == "Available")

            counts = {
                "total_slots": total_slots,
                "available_now": available_now,
                "physically_occupied": physically_occupied,
                "booked_reserved": booked_reserved,
                "sensor_errors": sensor_errors,
                # For compatibility with simple cards:
                "total": total_slots,
                "available": available_now,
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
