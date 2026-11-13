-- ====================================================================
-- Smart Parking System - MySQL Production Database Schema
-- Compatible with MySQL 8.0+ / MariaDB 10.5+
-- ====================================================================

CREATE DATABASE IF NOT EXISTS smartparking CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE smartparking;

-- 1. Parking Slots Table
CREATE TABLE IF NOT EXISTS parking_slots (
    id INT PRIMARY KEY,
    slot_number VARCHAR(16) NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. Sensor Status Table (Real-time hardware status)
CREATE TABLE IF NOT EXISTS sensor_status (
    slot_id INT PRIMARY KEY,
    physical_status VARCHAR(20) NOT NULL DEFAULT 'Empty', -- 'Empty', 'Occupied', 'Error'
    distance_cm FLOAT DEFAULT NULL,
    last_heartbeat DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_healthy TINYINT(1) NOT NULL DEFAULT 1,
    FOREIGN KEY (slot_id) REFERENCES parking_slots(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. Sensor Audit Log Table (Historical measurements)
CREATE TABLE IF NOT EXISTS sensor_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    slot_id INT NOT NULL,
    status VARCHAR(20) NOT NULL,
    distance_cm FLOAT DEFAULT NULL,
    recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_slot_recorded (slot_id, recorded_at),
    FOREIGN KEY (slot_id) REFERENCES parking_slots(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. Bookings Table
CREATE TABLE IF NOT EXISTS bookings (
    booking_id VARCHAR(36) PRIMARY KEY,
    slot_id INT NOT NULL,
    booking_date DATE NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    start_datetime DATETIME NOT NULL,
    end_datetime DATETIME NOT NULL,
    duration_minutes INT NOT NULL,
    booking_status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE', -- 'ACTIVE', 'CANCELLED', 'COMPLETED'
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_slot_status_interval (slot_id, booking_status, start_datetime, end_datetime),
    INDEX idx_date (booking_date),
    INDEX idx_booking_status (booking_status),
    FOREIGN KEY (slot_id) REFERENCES parking_slots(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Seed Default Parking Slots (Slots 1, 2, 3)
INSERT INTO parking_slots (id, slot_number, is_active)
VALUES 
    (1, 'Slot 1', 1),
    (2, 'Slot 2', 1),
    (3, 'Slot 3', 1)
ON DUPLICATE KEY UPDATE slot_number=VALUES(slot_number);

-- Seed Initial Sensor Status for Slots
INSERT INTO sensor_status (slot_id, physical_status, distance_cm, last_heartbeat, is_healthy)
VALUES
    (1, 'Empty', 200.0, CURRENT_TIMESTAMP, 1),
    (2, 'Empty', 200.0, CURRENT_TIMESTAMP, 1),
    (3, 'Empty', 200.0, CURRENT_TIMESTAMP, 1)
ON DUPLICATE KEY UPDATE physical_status=physical_status;
