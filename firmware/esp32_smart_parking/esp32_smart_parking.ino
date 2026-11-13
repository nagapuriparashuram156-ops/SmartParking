/*
  =============================================================================
  Smart Parking System - ESP32 Firmware
  =============================================================================
  Target Board: ESP32 DevKit V1 (ESP-WROOM-32)
  Sensors:      3x HC-SR04 Ultrasonic Distance Sensors
  Backend:      FastAPI (Hosted on Render via HTTPS or local IP via HTTP)
  Protocol:     Wi-Fi + REST API (JSON payload over HTTPS/HTTP)
  
  IMPORTANT HARDWARE NOTICE (VOLTAGE DIVIDER REQUIRED):
  HC-SR04 ultrasonic sensors operate at 5V VCC and output a 5V TTL pulse on ECHO.
  ESP32 GPIO pins are 3.3V logic ONLY and are NOT 5V tolerant!
  Connecting 5V ECHO directly to an ESP32 GPIO will damage the microcontroller.
  
  VOLTAGE DIVIDER PER SENSOR ECHO PIN:
      Sensor ECHO Pin (5V)
             |
            [R1: 1k Ohm]
             |
             +-----> ESP32 ECHO GPIO (Reads ~3.33V safely)
             |
            [R2: 2k Ohm] (or 2.2k / 4.7k Ohm)
             |
            GND
  
  PIN ASSIGNMENT:
  - Sensor 1 (Slot 1): TRIG = GPIO 5,  ECHO = GPIO 18 (via voltage divider)
  - Sensor 2 (Slot 2): TRIG = GPIO 19, ECHO = GPIO 21 (via voltage divider)
  - Sensor 3 (Slot 3): TRIG = GPIO 22, ECHO = GPIO 23 (via voltage divider)
  =============================================================================
*/

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

// =============================================================================
// CONFIGURATION (Adjust for your Wi-Fi and deployment)
// =============================================================================

// Wi-Fi Credentials
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// Backend API URL
// For Render Production HTTPS:
const char* BACKEND_BASE_URL = "https://smartparking-backend-4ivz.onrender.com";
// For Local Testing (replace with your computer's local IP, e.g. "http://192.168.1.100:8000"):
// const char* BACKEND_BASE_URL = "http://192.168.1.100:8000";

// Sensor API Secret Authentication Token (Must match SENSOR_API_TOKEN in backend)
const char* SENSOR_API_TOKEN = "smartpark_esp32_secret_2026";

// Timing & Reporting Configurations
const unsigned long REPORT_INTERVAL_MS = 3000;   // Periodic heartbeat report (3 seconds)
const unsigned long SENSOR_TIMEOUT_US  = 30000;  // 30ms timeout for pulseIn (~500cm max range)
const int REQUIRED_STABLE_READINGS     = 3;      // Consecutive identical readings required to confirm state change

// Distance Thresholds in Centimeters
const float VEHICLE_DETECTED_MAX_CM = 15.0;  // Distance <= 15cm means a vehicle is parked
const float EMPTY_SLOT_MIN_CM       = 15.1;  // Distance > 15cm and <= 250cm means slot is empty
const float SENSOR_MAX_VALID_CM     = 250.0; // Above 250cm is treated as out of range/error

// =============================================================================
// PIN DEFINITIONS
// =============================================================================
struct SensorPins {
  int slot_id;
  int trig_pin;
  int echo_pin;
};

const int NUM_SLOTS = 3;
const SensorPins SENSORS[NUM_SLOTS] = {
  { 1, 5,  18 }, // Slot 1: TRIG = GPIO 5,  ECHO = GPIO 18
  { 2, 19, 21 }, // Slot 2: TRIG = GPIO 19, ECHO = GPIO 21
  { 3, 22, 23 }  // Slot 3: TRIG = GPIO 22, ECHO = GPIO 23
};

// =============================================================================
// STATE TRACKING & HYSTERESIS
// =============================================================================
struct SlotState {
  String published_status;    // "Empty", "Occupied", "Error"
  String candidate_status;    // Tentative status under verification
  int candidate_count;        // Consecutive count of candidate status
  float last_distance_cm;
  unsigned long last_sent_ms;
};

SlotState slot_states[NUM_SLOTS];
unsigned long last_loop_time = 0;

// =============================================================================
// HELPER FUNCTIONS
// =============================================================================

void connectToWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.println();
  Serial.print("[WiFi] Connecting to: ");
  Serial.println(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 25) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[WiFi] Connected successfully!");
    Serial.print("[WiFi] ESP32 IP Address: ");
    Serial.println(WiFi.localIP());
    Serial.print("[WiFi] Signal Strength (RSSI): ");
    Serial.print(WiFi.RSSI());
    Serial.println(" dBm");
  } else {
    Serial.println("\n[WiFi ERROR] Failed to connect. Will retry during loop.");
  }
}

/**
 * Reads the HC-SR04 ultrasonic distance in centimeters.
 * Returns -1.0 if sensor is disconnected or timeout occurs (Sensor Error).
 */
float readUltrasonicDistance(int trigPin, int echoPin) {
  // Ensure trigger pin is low
  digitalWrite(trigPin, LOW);
  delayMicroseconds(4);

  // Send a 10 microsecond HIGH pulse
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  // Measure round-trip echo pulse duration
  // Sound travels at ~0.0343 cm/microsecond (round-trip factor: duration / 58.2)
  unsigned long duration_us = pulseIn(echoPin, HIGH, SENSOR_TIMEOUT_US);

  if (duration_us == 0) {
    // Timeout or sensor not responding
    return -1.0f;
  }

  float distance_cm = duration_us / 58.2f;

  if (distance_cm < 1.0f || distance_cm > SENSOR_MAX_VALID_CM) {
    return -1.0f;
  }

  return distance_cm;
}

/**
 * Evaluates raw distance into a discrete state:
 * - "Occupied" (Vehicle detected)
 * - "Empty" (Slot clear)
 * - "Error" (Sensor timeout or disconnected)
 */
String evaluateRawStatus(float distance_cm) {
  if (distance_cm < 0.0f) {
    return "Error";
  }
  if (distance_cm <= VEHICLE_DETECTED_MAX_CM) {
    return "Occupied";
  }
  return "Empty";
}

/**
 * Sends sensor telemetry to FastAPI backend via HTTP/HTTPS POST.
 */
bool sendTelemetry(int slot_id, const String& status, float distance_cm) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[HTTP WARNING] Wi-Fi not connected. Skipping telemetry transmission.");
    return false;
  }

  String url = String(BACKEND_BASE_URL) + "/sensor/update";
  HTTPClient http;

  // Render uses HTTPS (TLS 1.2/1.3)
  if (url.startsWith("https://")) {
    WiFiClientSecure* secureClient = new WiFiClientSecure;
    if (secureClient) {
      secureClient->setInsecure(); // Bypass CA certificate bundle check
      http.begin(*secureClient, url);
    } else {
      Serial.println("[HTTP ERROR] Unable to allocate secure client.");
      return false;
    }
  } else {
    WiFiClient client;
    http.begin(client, url);
  }

  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Sensor-Token", SENSOR_API_TOKEN);
  http.setTimeout(7000); // 7 second timeout for cloud latency

  // Build JSON payload
  String payload = "{";
  payload += "\"slot_id\":" + String(slot_id) + ",";
  payload += "\"status\":\"" + status + "\"";
  if (distance_cm >= 0.0f) {
    payload += ",\"distance_cm\":" + String(distance_cm, 1);
  } else {
    payload += ",\"distance_cm\":null";
  }
  payload += "}";

  int httpCode = http.POST(payload);
  bool success = false;

  if (httpCode == HTTP_CODE_OK || httpCode == HTTP_CODE_CREATED) {
    Serial.printf("[HTTP 200] Slot %d -> %s (%.1f cm)\n", slot_id, status.c_str(), distance_cm);
    success = true;
  } else if (httpCode > 0) {
    Serial.printf("[HTTP ERROR %d] Slot %d payload rejected: %s\n", httpCode, slot_id, http.getString().c_str());
  } else {
    Serial.printf("[HTTP FAILED] Slot %d connection error: %s\n", slot_id, http.errorToString(httpCode).c_str());
  }

  http.end();
  return success;
}

// =============================================================================
// SETUP
// =============================================================================
void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\n=======================================================");
  Serial.println("  Smart Parking System - ESP32 Firmware Starting");
  Serial.println("=======================================================");
  Serial.printf("Configured Slots: %d\n", NUM_SLOTS);
  Serial.printf("Vehicle Detection Threshold: <= %.1f cm\n", VEHICLE_DETECTED_MAX_CM);
  Serial.printf("Backend URL: %s\n", BACKEND_BASE_URL);

  // Initialize GPIO pins
  for (int i = 0; i < NUM_SLOTS; i++) {
    pinMode(SENSORS[i].trig_pin, OUTPUT);
    digitalWrite(SENSORS[i].trig_pin, LOW);
    pinMode(SENSORS[i].echo_pin, INPUT);

    slot_states[i].published_status = "Unknown";
    slot_states[i].candidate_status = "Unknown";
    slot_states[i].candidate_count  = 0;
    slot_states[i].last_distance_cm = -1.0f;
    slot_states[i].last_sent_ms     = 0;

    Serial.printf("Slot %d initialized: TRIG=GPIO %d, ECHO=GPIO %d\n",
                  SENSORS[i].slot_id, SENSORS[i].trig_pin, SENSORS[i].echo_pin);
  }

  connectToWiFi();
}

// =============================================================================
// MAIN LOOP
// =============================================================================
void loop() {
  unsigned long current_ms = millis();

  // 1. Maintain Wi-Fi Connection
  if (WiFi.status() != WL_CONNECTED) {
    connectToWiFi();
  }

  // 2. Poll and Process All 3 Ultrasonic Sensors
  for (int i = 0; i < NUM_SLOTS; i++) {
    int slot_id = SENSORS[i].slot_id;

    // Small delay between sensor firings to prevent acoustic echo cross-talk
    delay(40);

    float distance = readUltrasonicDistance(SENSORS[i].trig_pin, SENSORS[i].echo_pin);
    String raw_status = evaluateRawStatus(distance);

    // Hysteresis & Debouncing: Require consecutive identical readings before state change
    if (raw_status == slot_states[i].candidate_status) {
      slot_states[i].candidate_count++;
    } else {
      slot_states[i].candidate_status = raw_status;
      slot_states[i].candidate_count  = 1;
    }

    bool state_changed = false;
    if (slot_states[i].candidate_count >= REQUIRED_STABLE_READINGS &&
        slot_states[i].candidate_status != slot_states[i].published_status) {
      slot_states[i].published_status = slot_states[i].candidate_status;
      state_changed = true;
      Serial.printf("[STATE CHANGE] Slot %d verified new status: %s (%.1f cm)\n",
                    slot_id, slot_states[i].published_status.c_str(), distance);
    }

    slot_states[i].last_distance_cm = distance;

    // Send update if:
    // a) State changed (immediate transmission)
    // b) Periodic reporting interval elapsed (heartbeat telemetry)
    bool time_to_report = (current_ms - slot_states[i].last_sent_ms >= REPORT_INTERVAL_MS);

    if (state_changed || time_to_report) {
      bool sent = sendTelemetry(slot_id, slot_states[i].published_status, distance);
      if (sent) {
        slot_states[i].last_sent_ms = current_ms;
      }
    }
  }

  // Loop pacing delay
  delay(100);
}
