# Smart Parking System (ESP32 + Ultrasonic Sensors + FastAPI + MySQL + React + Render)

A complete, production-grade, end-to-end IoT Smart Parking System connecting physical ultrasonic sensors to an ESP32 microcontroller, streaming real-time telemetry over Wi-Fi/HTTPS to a Python FastAPI backend, persisting transactional booking records and sensor audit logs in MySQL, and displaying live availability on a modern React + Vite dashboard.

---

## 1. Project Overview & Architecture

### System Architecture Flow

```
+-----------------------------------------------------------------------------------------+
|                                    PHYSICAL HARDWARE                                    |
|                                                                                         |
|  [Slot 1: HC-SR04]         [Slot 2: HC-SR04]         [Slot 3: HC-SR04]                   |
|   TRIG: GPIO 5              TRIG: GPIO 19             TRIG: GPIO 22                     |
|   ECHO: GPIO 18 (1k/2k Div) ECHO: GPIO 21 (1k/2k Div) ECHO: GPIO 23 (1k/2k Div)         |
|         \                         |                         /                           |
|          +------------------------+------------------------+                            |
|                                   |                                                     |
|                                   v                                                     |
|                      ESP32 DevKit V1 Microcontroller                                    |
|                      - 3-sample hysteresis / debounce filter                            |
|                      - Wi-Fi auto-reconnection with backoff                             |
|                      - WiFiClientSecure (HTTPS) + Token Auth                            |
+-----------------------------------|-----------------------------------------------------+
                                    |
                       HTTPS POST /sensor/update
                        (X-Sensor-Token Header)
                                    |
                                    v
+-----------------------------------------------------------------------------------------+
|                                  CLOUD BACKEND (Render)                                 |
|                                                                                         |
|                     FastAPI Web Service (Python 3.10)                                   |
|                     - Sensor data ingestion & normalization                             |
|                     - Time-window booking manager with 1-min buffer                     |
|                     - Conflict detection & transactional safety                         |
|                     - Asia/Kolkata timezone standardizer                                |
|                     - Real-time dashboard KPI metrics aggregator                        |
+-----------------------------------|-----------------------------------------------------+
                                    |
                  SQL Queries / Connection Pool (PyMySQL)
                                    |
                                    v
+-----------------------------------------------------------------------------------------+
|                                  DATABASE PERSISTENCE                                   |
|                                                                                         |
|                     MySQL 8.0+ Database (InnoDB)                                        |
|                     - `parking_slots` (id, slot_number, is_active)                      |
|                     - `sensor_status` (physical_status, distance_cm, last_heartbeat)    |
|                     - `sensor_logs` (historical audit trail)                           |
|                     - `bookings` (intervals, status, duration, foreign keys)            |
+-----------------------------------|-----------------------------------------------------+
                                    |
                        GET /slots | GET /bookings
                        (3.5s periodic polling / JSON)
                                    |
                                    v
+-----------------------------------------------------------------------------------------+
|                                  FRONTEND CLIENT (Render)                               |
|                                                                                         |
|                     React 18 + Vite Web Application                                     |
|                     - Live KPI metrics (Available Now, Occupied, Reserved, Errors)      |
|                     - Dynamic 3-bay real-time status cards                              |
|                     - Time-window reservation modal with buffer guidance                |
|                     - Booking cancellation management table                             |
|                     - High-resolution dynamic mobile QR code generator                  |
+-----------------------------------------------------------------------------------------+
```

---

## 2. Hardware Components & Connection Table

### Hardware Bill of Materials (BOM)
1. **1x ESP32 DevKit V1** (30-pin or 36-pin ESP-WROOM-32)
2. **3x HC-SR04** (or HC-SR04P / JSN-SR04T) Ultrasonic Distance Sensors
3. **3x $1\text{ k}\Omega$ Resistors** (for voltage divider $R_1$)
4. **3x $2\text{ k}\Omega$ Resistors** (or $2.2\text{ k}\Omega / 4.7\text{ k}\Omega$, for voltage divider $R_2$)
5. **Breadboard & Jumper Wires** (Male-to-Male, Male-to-Female)
6. **Micro-USB Cable** (data-capable) & 5V/2A USB Power Adapter

---

## 3. Voltage Divider Circuit & ESP32 Pin Configuration

> [!CAUTION]
> **CRITICAL 5V to 3.3V VOLTAGE WARNING**:
> Standard HC-SR04 ultrasonic sensors require 5V VCC and output a **5V TTL logic pulse** on their ECHO pin.
> ESP32 GPIO pins are rated for **3.3V logic maximum** and are **NOT 5V tolerant**. Connecting a 5V ECHO output directly to an ESP32 GPIO can permanently destroy the pin or the entire SoC.

### Voltage Divider Circuit Diagram per Sensor:

```
        HC-SR04 ECHO Pin (5V Pulse)
                    |
                    |
               [ R1 = 1 kΩ ]
                    |
                    +--------------------------> ESP32 GPIO (Reads ~3.33V Safe Logic)
                    |
               [ R2 = 2 kΩ ]
                    |
                    |
                   GND ------------------------> ESP32 GND & Power Supply GND
```

**Calculation:**
$$V_{out} = V_{in} \times \frac{R_2}{R_1 + R_2} = 5.0\text{V} \times \frac{2000}{1000 + 2000} = 3.333\text{V}$$
*(Alternatively: $R_1 = 2.2\text{ k}\Omega$ and $R_2 = 4.7\text{ k}\Omega$ yields $5.0\text{V} \times \frac{4700}{6900} = 3.40\text{V}$, which is also safe).*

### ESP32 Pin Connection Table

| Ultrasonic Sensor | Sensor Pin | Connection Target | Protection / Component |
| :--- | :--- | :--- | :--- |
| **All Sensors** | VCC | ESP32 **VIN** (5V from USB) or external 5V | Direct |
| **All Sensors** | GND | ESP32 **GND** | Direct (Common ground) |
| **Sensor 1 (Slot 1)** | TRIG | ESP32 **GPIO 5** | Direct (Output) |
| **Sensor 1 (Slot 1)** | ECHO | ESP32 **GPIO 18** | **Through $1\text{ k}\Omega / 2\text{ k}\Omega$ Voltage Divider** |
| **Sensor 2 (Slot 2)** | TRIG | ESP32 **GPIO 19** | Direct (Output) |
| **Sensor 2 (Slot 2)** | ECHO | ESP32 **GPIO 21** | **Through $1\text{ k}\Omega / 2\text{ k}\Omega$ Voltage Divider** |
| **Sensor 3 (Slot 3)** | TRIG | ESP32 **GPIO 22** | Direct (Output) |
| **Sensor 3 (Slot 3)** | ECHO | ESP32 **GPIO 23** | **Through $1\text{ k}\Omega / 2\text{ k}\Omega$ Voltage Divider** |

*Note: Pins GPIO 6 to 11 are connected to internal SPI flash and must never be used. Strapping pins (GPIO 0, 2, 12, 15) are avoided to prevent boot-mode conflicts.*

---

## 4. ESP32 Sensor Detection & Telemetry Logic

1. **Measurement**: Each sensor is triggered with a 10 $\mu\text{s}$ pulse and the echo duration is measured via `pulseIn(ECHO, HIGH, 30000)` (30 ms timeout, preventing blocking).
2. **Distance Calculation**: $\text{Distance (cm)} = \frac{\text{Duration in }\mu\text{s}}{58.2}$.
3. **Thresholds**:
   - $\le 15.0\text{ cm}$: **Occupied** (Vehicle is physically present in the bay)
   - $> 15.0\text{ cm}$ and $\le 250.0\text{ cm}$: **Empty** (Bay is clear)
   - $0.0\text{ cm}$ (timeout) or $> 400.0\text{ cm}$: **Error** (Sensor disconnected or signal lost — never mistakenly marked as Empty!)
4. **Hysteresis & Debouncing**: Requires **3 consecutive identical state samples** before publishing a state transition, filtering out transient acoustic noise and people walking past.
5. **Heartbeat & Event Push**: Sends telemetry immediately upon confirmed state change, and emits a periodic heartbeat every 3000 ms.
6. **Secure Telemetry**: Connects via HTTPS to Render with header `X-Sensor-Token: smartpark_esp32_secret_2026`.

---

## 5. Backend Architecture & MySQL Persistence

### Database Schema (MySQL 8.0+ / MariaDB)

The schema is defined in [backend/schema.sql](file:///C:/Users/nagap/OneDrive/Desktop/tbp%20project/backend/schema.sql):

```sql
CREATE DATABASE IF NOT EXISTS smartparking CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE smartparking;

CREATE TABLE IF NOT EXISTS parking_slots (
    id INT PRIMARY KEY,
    slot_number VARCHAR(16) NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sensor_status (
    slot_id INT PRIMARY KEY,
    physical_status VARCHAR(20) NOT NULL DEFAULT 'Empty',
    distance_cm FLOAT DEFAULT NULL,
    last_heartbeat DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_healthy TINYINT(1) NOT NULL DEFAULT 1,
    FOREIGN KEY (slot_id) REFERENCES parking_slots(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sensor_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    slot_id INT NOT NULL,
    status VARCHAR(20) NOT NULL,
    distance_cm FLOAT DEFAULT NULL,
    recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_slot_rec (slot_id, recorded_at),
    FOREIGN KEY (slot_id) REFERENCES parking_slots(id) ON DELETE CASCADE
);

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
);
```

### Dashboard Metric Calculations (Bug Fix Explanation)

Previously, the dashboard returned `Available Slots: 3` and `Booked / Occupied: 0` even after 2 bookings because the old backend hardcoded `status = "Available"` unless the sensor was occupied, and the frontend only calculated `slots.length - available`.

The updated engine accurately computes discrete categories based on `Asia/Kolkata` wall-clock time $T_{now}$:
- **Total Slots**: Total configured bays (3).
- **Physically Occupied**: Slots where ultrasonic sensor detects a vehicle ($d \le 15\text{ cm}$) right now.
- **Booked / Reserved**: Slots having an active reservation right now ($\text{start} \le T_{now} < \text{end}$).
- **Available Now**: Slots with NO vehicle detected, NO active reservation covering $T_{now}$, and NO sensor error.
- **Sensor Errors**: Slots where sensor is timed out, disconnected, or reported "Error".

### 1-Minute Buffer & Conflict Prevention Rules:
For any new booking interval $[S_{new}, E_{new}]$ on slot $K$:
1. $S_{new}$ cannot be in the past: $S_{new} \ge T_{now} - 60\text{s}$.
2. Duration must satisfy $1 \le \text{duration} \le 1440$ minutes.
3. If $S_{new} \approx T_{now}$ and the slot is physically occupied, booking is rejected.
4. For all existing active bookings $[S_{exist}, E_{exist}]$ on slot $K$, a 1-minute buffer is applied:
   $$\text{Conflict exists if } S_{new} < (E_{exist} + 1\text{ min}) \quad\text{and}\quad E_{new} > (S_{exist} - 1\text{ min})$$
5. Distinct slots may be reserved at the exact same time without conflict.

---

## 6. REST API Endpoints Reference

Base URL (Production): `https://smartparking-backend-4ivz.onrender.com`

| Method | Endpoint | Description | Auth Required | Parameters / Body |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/` | Root service status and endpoint index | No | None |
| `GET` | `/health` | Health check (API + DB connection + Server time) | No | None |
| `GET` | `/slots` | Real-time slots status and KPI summary metrics | No | None |
| `GET` | `/sensor/status` | Detailed telemetry for all 3 sensors | No | None |
| `POST` | `/sensor/update` | Ingests telemetry from ESP32 | **Yes** (`X-Sensor-Token`) | `{"slot_id": 1, "status": "Occupied", "distance_cm": 8.5}` |
| `POST` | `/book/{slot_id}` | Creates a slot reservation with buffer checks | No | Query/JSON: `date`, `start_time`, `duration_minutes` |
| `GET` | `/bookings` | Lists all reservations | No | Query: `slot_id`, `status` |
| `GET` | `/bookings/{id}` | Fetches single booking details | No | Path: `id` |
| `DELETE`| `/bookings/{id}` | Cancels a booking by Booking ID | No | Path: `id` |
| `POST` | `/cancel/{slot_id}` | Cancels booking for a slot | No | Query/JSON: `booking_id`, `date`, `time` |

---

## 7. Environment Variables Configuration

### Backend Environment Variables (Render Dashboard / `.env`)

| Variable Name | Description | Example / Recommended Value |
| :--- | :--- | :--- |
| `MYSQL_HOST` | MySQL database host (e.g., Aiven, TiDB, Render) | `mysql-xxxx.aivencloud.com` |
| `MYSQL_PORT` | MySQL port | `3306` |
| `MYSQL_USER` | MySQL username | `avnadmin` |
| `MYSQL_PASSWORD` | MySQL password | `your_secret_password` |
| `MYSQL_DATABASE` | MySQL database name | `smartparking` |
| `SENSOR_API_TOKEN`| Secret token required for ESP32 `/sensor/update` | `smartpark_esp32_secret_2026` |
| `FRONTEND_ORIGINS`| Allowed origins for CORS (comma-separated) | `https://smartparking-mobile.onrender.com` |

*Note: If MySQL credentials are not provided in local development, the backend automatically utilizes SQLite fallback (`smartparking.db`) so developers can test without external databases.*

### Frontend Environment Variables (Vite / Render Static Site)

| Variable Name | Description | Default Value |
| :--- | :--- | :--- |
| `VITE_API_URL` | Base URL of deployed FastAPI backend | `https://smartparking-backend-4ivz.onrender.com` |
| `VITE_WEBSITE_URL` | Public frontend URL for QR code generation | `https://smartparking-mobile.onrender.com` |

---

## 8. Step-by-Step Installation & Local Run Guide

### A. Backend Installation & Run
```bash
# 1. Navigate to backend directory
cd backend

# 2. Activate virtual environment
# Windows:
.\venv\Scripts\activate
# Linux/macOS:
# source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run automated test suite
pytest test_backend.py -v

# 5. Start FastAPI local server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### B. Frontend Installation & Run
```bash
# 1. In project root:
npm install

# 2. Build for production to verify bundling:
npm run build

# 3. Run Vite local development server:
npm run dev
```

### C. ESP32 Firmware Upload (Arduino IDE)
1. Open the Arduino IDE (v2.0+ recommended).
2. Install **ESP32 board package**:
   - Go to **File -> Preferences -> Additional Board Manager URLs**:
     `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
   - Open **Tools -> Board -> Boards Manager**, search for `esp32`, and install `esp32 by Espressif Systems`.
3. Open firmware sketch:
   [firmware/esp32_smart_parking/esp32_smart_parking.ino](file:///C:/Users/nagap/OneDrive/Desktop/tbp%20project/firmware/esp32_smart_parking/esp32_smart_parking.ino).
4. Update `WIFI_SSID` and `WIFI_PASSWORD` with your 2.4 GHz Wi-Fi credentials.
5. Select Board: **Tools -> Board -> ESP32 Arduino -> ESP32 Dev Module**.
6. Select your COM port under **Tools -> Port**.
7. Click **Upload**.
8. Open **Serial Monitor** at **115200 baud** to view real-time Wi-Fi connection and sensor telemetry logs.

---

## 9. Render Cloud Deployment Instructions

### Deploying the Backend (Web Service):
1. In the [Render Dashboard](https://dashboard.render.com), select your existing service: `smartparking-backend-4ivz`.
2. Ensure settings match:
   - **Environment**: Python
   - **Root Directory**: `backend`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
3. Under **Environment Variables**, add:
   - `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`
   - `SENSOR_API_TOKEN` = `smartpark_esp32_secret_2026`
   - `FRONTEND_ORIGINS` = `https://smartparking-mobile.onrender.com,https://smartparking-39mz.onrender.com`
4. Trigger manual deploy or push commit to GitHub `main` branch.

### Deploying the Frontend (Static Site):
1. In Render Dashboard, select existing static site: `smartparking-mobile`.
2. Ensure settings match:
   - **Build Command**: `npm install && npm run build`
   - **Publish Directory**: `dist`
3. Under **Environment Variables**, add:
   - `VITE_API_URL` = `https://smartparking-backend-4ivz.onrender.com`
   - `VITE_WEBSITE_URL` = `https://smartparking-mobile.onrender.com`
4. Under **Redirects/Rewrites**:
   - Rewrite `/*` -> `/index.html` (Status 200).

---

## 10. Troubleshooting Guide

| Issue | Root Cause | Solution |
| :--- | :--- | :--- |
| **Dashboard counts show 0 Booked even after booking** | Slot effective status logic bug | **Fixed**: Effective status properly aggregates active reservations, physical vehicle detection, and errors. |
| **ESP32 resets repeatedly or fails to boot** | Strapping pins (GPIO 12/0) pulled high/low or 5V connected directly | Connect ECHO pins through $1\text{ k}\Omega / 2\text{ k}\Omega$ voltage dividers to GPIO 18, 21, 23. Avoid GPIO 12. |
| **Ultrasonic reading returns 0 or -1 cm** | Sensor disconnected, missing echo pulse, or beyond 250 cm | Check VCC (5V), GND, and voltage divider breadboard connections. Verify HC-SR04 power supply has common ground with ESP32. |
| **HTTP 401 on `/sensor/update`** | Missing or mismatched `X-Sensor-Token` | Verify that `SENSOR_API_TOKEN` in ESP32 firmware matches backend environment variable. |
| **Render frontend cannot communicate with backend** | CORS origin blocking or backend cold-start | CORS middleware now accepts regex for all `*.onrender.com` subdomains. Render free tier spins down after inactivity; allow ~40 seconds for instance wake-up. |
| **Cannot book a slot ending at 10:00 when next booking starts at 10:00** | 1-minute safety buffer enforcement | By design, reservations require at least a 1-minute buffer between bookings to prevent vehicle transition collisions. Select 10:01 or later. |
