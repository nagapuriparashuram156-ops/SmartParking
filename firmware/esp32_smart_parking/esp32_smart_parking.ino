/*
  =============================================================================
  Smart Parking System - ESP32 Firmware with Dual Red/Green LED Bay Indicators
  =============================================================================
  Target Board: ESP32 DevKit V1 (ESP-WROOM-32)
  Sensors:      3x HC-SR04 Ultrasonic Distance Sensors
  Indicators:   3x Red LEDs (Occupied) + 3x Green LEDs (Available / Empty)
  Backend:      FastAPI (Hosted on Render via HTTPS or local IP via HTTP)
  Protocol:     Wi-Fi + REST API (JSON payload over HTTPS/HTTP)
  
  =============================================================================
  HARDWARE WIRING & SCHEMATIC REFERENCE
  =============================================================================
  
  1. HC-SR04 ULTRASONIC SENSORS (VOLTAGE DIVIDER REQUIRED ON ECHO):
     HC-SR04 operates at 5V VCC and its ECHO pin outputs a 5V TTL pulse.
     ESP32 GPIO pins are 3.3V logic ONLY and NOT 5V tolerant!
     Connecting 5V ECHO directly to an ESP32 GPIO will damage the microcontroller.
     
     VOLTAGE DIVIDER ON EACH ECHO PIN:
         HC-SR04 ECHO Pin (5V)
                |
               [R1: 1k Ohm]
                |
                +-----> ESP32 ECHO GPIO (Reads ~3.33V safely)
                |
               [R2: 2k Ohm] (or 2.2k Ohm)
                |
               GND (Common Ground)

  2. RED AND GREEN LED INDICATORS (CURRENT-LIMITING RESISTORS REQUIRED):
     Each LED MUST have a 220 Ohm - 330 Ohm resistor in series to limit
     current to ~10-15 mA and protect the ESP32 GPIO pins and LEDs.
     Never connect LEDs directly to GPIO pins without resistors!
     
     WIRING PER LED:
         ESP32 GPIO ----> [220 Ohm Resistor] ----> LED Anode (+)
                                                  LED Cathode (-) ----> GND

  =============================================================================
  EXACT PIN ASSIGNMENT TABLE (AS SPECIFIED):
  =============================================================================
  Bay / Slot | Function       | ESP32 GPIO | Description
  -----------+----------------+------------+-----------------------------------
  Bay #1     | Sensor TRIG 1  | GPIO 5     | HC-SR04 Trigger (Output)
  Bay #1     | Sensor ECHO 1  | GPIO 34    | HC-SR04 Echo (Input via Divider)
  Bay #1     | RED LED 1      | GPIO 25    | Physical Occupancy Indicator
  Bay #1     | GREEN LED 1    | GPIO 26    | Physical Available Indicator
  -----------+----------------+------------+-----------------------------------
  Bay #2     | Sensor TRIG 2  | GPIO 18    | HC-SR04 Trigger (Output)
  Bay #2     | Sensor ECHO 2  | GPIO 35    | HC-SR04 Echo (Input via Divider)
  Bay #2     | RED LED 2      | GPIO 27    | Physical Occupancy Indicator
  Bay #2     | GREEN LED 2    | GPIO 14    | Physical Available Indicator
  -----------+----------------+------------+-----------------------------------
  Bay #3     | Sensor TRIG 3  | GPIO 19    | HC-SR04 Trigger (Output)
  Bay #3     | Sensor ECHO 3  | GPIO 32    | HC-SR04 Echo (Input via Divider)
  Bay #3     | RED LED 3      | GPIO 16    | Physical Occupancy Indicator
  Bay #3     | GREEN LED 3    | GPIO 17    | Physical Available Indicator
  =============================================================================
  
  LED INDICATION LOGIC (INDEPENDENT PER BAY):
  - Distance below 10 cm (Occupied):      RED ON,  GREEN OFF  -> Status = Occupied
  - Distance 10 cm or greater (Empty):    RED OFF, GREEN ON   -> Status = Empty
  - Sensor Error / Disconnected:          RED OFF, GREEN OFF  -> Status = Sensor Error
  
  NON-BLOCKING ARCHITECTURE (FreeRTOS Dual-Core):
  - Core 1: High-speed sensor sampling & instant local LED switching (< 200ms)
  - Core 0: Background telemetry worker queue for HTTPS/HTTP communication with Render
  - HTTPS requests to Render NEVER block sensor sampling or LED state changes!
  =============================================================================
*/

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

// =============================================================================
// CONFIGURATION & CREDENTIALS
// =============================================================================

// Wi-Fi Credentials
// For local testing, configure your mobile hotspot SSID and Password:
// SSID: "ko_102 2G" | Password: "123456789"
#ifndef WIFI_SSID
#define WIFI_SSID "ko_102 2G"
#endif

#ifndef WIFI_PASSWORD
#define WIFI_PASSWORD "123456789"
#endif

// Backend API URL (Render Cloud Service)
const char* BACKEND_BASE_URL = "https://smartparking-backend-4ivz.onrender.com";
// For Local Development on same network:
// const char* BACKEND_BASE_URL = "http://192.168.1.100:8000";

// Sensor API Secret Authentication Token (Matches SENSOR_API_TOKEN in backend)
const char* SENSOR_API_TOKEN = "smartpark_esp32_secret_2026";

// Exact Detection Thresholds (in centimeters)
const float DETECTION_THRESHOLD_CM = 10.0f; // < 10.0 cm is Occupied, >= 10.0 cm is Empty
const float SENSOR_MAX_VALID_CM    = 400.0f; // Above 400 cm is treated as out of range/error
const unsigned long SENSOR_TIMEOUT_US = 25000; // 25ms timeout (~400cm max range)

// Timing Configurations
const unsigned long HEARTBEAT_INTERVAL_MS = 5000; // Periodic heartbeat every 5 seconds
const int REQUIRED_STABLE_READINGS        = 2;    // 2 consecutive readings (~120ms) for debounce

// =============================================================================
// HARDWARE PIN MAPPING
// =============================================================================
struct BayHardware {
  int slot_id;
  int trig_pin;
  int echo_pin;
  int red_led_pin;
  int green_led_pin;
};

const int NUM_SLOTS = 3;
const BayHardware BAYS[NUM_SLOTS] = {
  { 1, 5,  34, 25, 26 }, // Bay #1: TRIG 5,  ECHO 34, RED 25, GREEN 26
  { 2, 18, 35, 27, 14 }, // Bay #2: TRIG 18, ECHO 35, RED 27, GREEN 14
  { 3, 19, 32, 16, 17 }  // Bay #3: TRIG 19, ECHO 32, RED 16, GREEN 17
};

// =============================================================================
// STATE TRACKING DATA STRUCTURES
// =============================================================================
struct SlotState {
  String published_status;    // "Empty", "Occupied", "Error"
  String candidate_status;    // Tentative status under verification
  int candidate_count;        // Consecutive count of candidate status
  float last_distance_cm;
  unsigned long last_sent_ms;
};

SlotState slot_states[NUM_SLOTS];

// Telemetry Queue Item for Non-blocking Core 0 Transmission
struct TelemetryMsg {
  int slot_id;
  char status[16]; // "Occupied", "Empty", "Error"
  bool occupied;
  float distance_cm;
};

QueueHandle_t telemetryQueue = NULL;
TaskHandle_t telemetryTaskHandle = NULL;

// =============================================================================
// LOCAL HARDWARE CONTROL (RUNS ON CORE 1 - FAST & NON-BLOCKING)
// =============================================================================

/**
 * Updates the physical LED indicators immediately on ESP32 GPIOs:
 * - Occupied: RED ON,  GREEN OFF
 * - Empty:    RED OFF, GREEN ON
 * - Error:    RED OFF, GREEN OFF (Safe fail-safe indication)
 */
void updateBayLEDs(int bayIndex, const String& status) {
  int redPin   = BAYS[bayIndex].red_led_pin;
  int greenPin = BAYS[bayIndex].green_led_pin;

  if (status == "Occupied") {
    digitalWrite(redPin, HIGH);
    digitalWrite(greenPin, LOW);
  } else if (status == "Empty") {
    digitalWrite(redPin, LOW);
    digitalWrite(greenPin, HIGH);
  } else {
    // Sensor Error / Disconnected / Out of range:
    // Both OFF to prevent false indication
    digitalWrite(redPin, LOW);
    digitalWrite(greenPin, LOW);
  }
}

/**
 * Reads HC-SR04 ultrasonic distance in centimeters.
 * Fast pulseIn with 25ms timeout (~400cm max range).
 * Returns -1.0 if disconnected, timed out, or invalid.
 */
float readUltrasonicDistance(int trigPin, int echoPin) {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(4);

  // Send 10 microsecond HIGH trigger pulse
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  // Measure round-trip echo pulse duration
  // Sound velocity ~0.0343 cm/microsecond (round-trip divider: 58.2)
  unsigned long duration_us = pulseIn(echoPin, HIGH, SENSOR_TIMEOUT_US);

  if (duration_us == 0) {
    return -1.0f; // Timeout / sensor disconnected
  }

  float distance_cm = duration_us / 58.2f;

  if (distance_cm < 1.0f || distance_cm > SENSOR_MAX_VALID_CM) {
    return -1.0f; // Out of valid measurement bounds
  }

  return distance_cm;
}

/**
 * Evaluates raw ultrasonic distance based on exact 10 cm threshold:
 * - Distance < 10.0 cm:          Occupied (RED ON, GREEN OFF)
 * - Distance >= 10.0 cm:         Empty (RED OFF, GREEN ON)
 * - Sensor Error (< 0):          Error (RED OFF, GREEN OFF)
 */
String evaluateRawStatus(float distance_cm) {
  if (distance_cm < 0.0f) {
    return "Error";
  }
  if (distance_cm < DETECTION_THRESHOLD_CM) {
    return "Occupied";
  }
  return "Empty";
}

// =============================================================================
// BACKGROUND TELEMETRY WORKER (RUNS ON CORE 0 - NON-BLOCKING)
// =============================================================================

/**
 * Handles Wi-Fi connection and re-connection on Core 0.
 */
void maintainWiFiConnection() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.printf("\n[WiFi Core 0] Connecting to SSID: %s\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long startAttempt = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startAttempt < 8000) {
    vTaskDelay(pdMS_TO_TICKS(400));
    Serial.print(".");
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[WiFi Core 0] Connected! IP: %s (RSSI: %d dBm)\n",
                  WiFi.localIP().toString().c_str(), WiFi.RSSI());
  } else {
    Serial.println("\n[WiFi Core 0] Connection pending. Retrying in background...");
  }
}

/**
 * Dispatches a single sensor telemetry packet to the FastAPI backend.
 */
bool postTelemetryToBackend(const TelemetryMsg& msg) {
  if (WiFi.status() != WL_CONNECTED) {
    return false;
  }

  String url = String(BACKEND_BASE_URL) + "/sensor/update";
  HTTPClient http;

  if (url.startsWith("https://")) {
    WiFiClientSecure* secureClient = new WiFiClientSecure;
    if (secureClient) {
      secureClient->setInsecure(); // Accept Render TLS certificate
      http.begin(*secureClient, url);
    } else {
      Serial.println("[HTTP Core 0] Error allocating secure client.");
      return false;
    }
  } else {
    WiFiClient client;
    http.begin(client, url);
  }

  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Sensor-Token", SENSOR_API_TOKEN);
  http.setTimeout(5000); // 5-second cloud HTTP timeout

  // Build JSON payload compatible with both boolean ("occupied": true/false)
  // and string ("status": "Occupied"/"Empty"/"Error")
  String payload = "{";
  payload += "\"slot_id\":" + String(msg.slot_id) + ",";
  payload += "\"status\":\"" + String(msg.status) + "\",";
  payload += "\"occupied\":" + String(msg.occupied ? "true" : "false");
  if (msg.distance_cm >= 0.0f) {
    payload += ",\"distance_cm\":" + String(msg.distance_cm, 1);
  } else {
    payload += ",\"distance_cm\":null";
  }
  payload += "}";

  int httpCode = http.POST(payload);
  bool success = false;

  if (httpCode == HTTP_CODE_OK || httpCode == HTTP_CODE_CREATED) {
    Serial.printf("[HTTP 200] Bay %d -> %s (%.1f cm)\n", msg.slot_id, msg.status, msg.distance_cm);
    success = true;
  } else if (httpCode > 0) {
    Serial.printf("[HTTP %d] Bay %d rejected: %s\n", httpCode, msg.slot_id, http.getString().c_str());
  } else {
    Serial.printf("[HTTP FAIL] Bay %d connection error: %s\n", msg.slot_id, http.errorToString(httpCode).c_str());
  }

  http.end();
  return success;
}

/**
 * FreeRTOS Background Task running on Core 0.
 * Polls the telemetryQueue and sends HTTP telemetry.
 * Never blocks Core 1 sensor readings or LED switching.
 */
void telemetryTask(void* parameter) {
  TelemetryMsg msg;

  for (;;) {
    maintainWiFiConnection();

    // Block waiting for new telemetry message in queue (up to 1000ms)
    if (xQueueReceive(telemetryQueue, &msg, pdMS_TO_TICKS(1000)) == pdPASS) {
      if (WiFi.status() == WL_CONNECTED) {
        postTelemetryToBackend(msg);
      }
    }

    // Yield CPU to FreeRTOS scheduler
    vTaskDelay(pdMS_TO_TICKS(50));
  }
}

/**
 * Enqueues a telemetry update to the FreeRTOS background queue.
 */
void enqueueTelemetry(int slot_id, const String& status, float distance_cm) {
  if (telemetryQueue == NULL) return;

  TelemetryMsg msg;
  msg.slot_id = slot_id;
  strncpy(msg.status, status.c_str(), sizeof(msg.status) - 1);
  msg.status[sizeof(msg.status) - 1] = '\0';
  msg.occupied = (status == "Occupied");
  msg.distance_cm = distance_cm;

  // Non-blocking send: if queue is full, overwrite or discard oldest
  if (xQueueSend(telemetryQueue, &msg, 0) != pdPASS) {
    // Queue full: discard oldest item and push latest
    TelemetryMsg dummy;
    xQueueReceive(telemetryQueue, &dummy, 0);
    xQueueSend(telemetryQueue, &msg, 0);
  }
}

// =============================================================================
// SETUP (RUNS AT POWER-ON)
// =============================================================================
void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\n=======================================================");
  Serial.println("  Smart Parking System - ESP32 Firmware Starting");
  Serial.println("  Dual Red/Green LED Bay Indicators • FreeRTOS Dual-Core");
  Serial.println("=======================================================");
  Serial.printf("Configured Bays: %d\n", NUM_SLOTS);
  Serial.printf("Detection Threshold: < %.1f cm (Occupied) | >= %.1f cm (Empty)\n",
                DETECTION_THRESHOLD_CM, DETECTION_THRESHOLD_CM);
  Serial.printf("Backend URL: %s\n", BACKEND_BASE_URL);

  // Initialize GPIO pins for sensors and LEDs
  for (int i = 0; i < NUM_SLOTS; i++) {
    // Sensor pins
    pinMode(BAYS[i].trig_pin, OUTPUT);
    digitalWrite(BAYS[i].trig_pin, LOW);
    pinMode(BAYS[i].echo_pin, INPUT);

    // LED indicator pins
    pinMode(BAYS[i].red_led_pin, OUTPUT);
    pinMode(BAYS[i].green_led_pin, OUTPUT);
    digitalWrite(BAYS[i].red_led_pin, LOW);
    digitalWrite(BAYS[i].green_led_pin, LOW);

    // State tracking initialization
    slot_states[i].published_status = "Unknown";
    slot_states[i].candidate_status = "Unknown";
    slot_states[i].candidate_count  = 0;
    slot_states[i].last_distance_cm = -1.0f;
    slot_states[i].last_sent_ms     = 0;

    Serial.printf("Bay #%d: TRIG=GPIO %d, ECHO=GPIO %d | RED_LED=GPIO %d, GREEN_LED=GPIO %d\n",
                  BAYS[i].slot_id, BAYS[i].trig_pin, BAYS[i].echo_pin,
                  BAYS[i].red_led_pin, BAYS[i].green_led_pin);
  }

  // Self-test LED sequence on boot (light all Red & Green LEDs for 500ms)
  Serial.println("[LED TEST] Running power-on LED self-test...");
  for (int i = 0; i < NUM_SLOTS; i++) {
    digitalWrite(BAYS[i].red_led_pin, HIGH);
    digitalWrite(BAYS[i].green_led_pin, HIGH);
  }
  delay(500);
  for (int i = 0; i < NUM_SLOTS; i++) {
    digitalWrite(BAYS[i].red_led_pin, LOW);
    digitalWrite(BAYS[i].green_led_pin, LOW);
  }
  Serial.println("[LED TEST] Power-on LED self-test completed.");

  // Create FreeRTOS Queue for non-blocking telemetry (capacity: 10 items)
  telemetryQueue = xQueueCreate(10, sizeof(TelemetryMsg));

  // Launch Telemetry Worker Task on Core 0 (leaving Core 1 dedicated to sensors & LEDs)
  xTaskCreatePinnedToCore(
    telemetryTask,        // Task function
    "TelemetryWorker",    // Task name
    8192,                 // Stack size in bytes
    NULL,                 // Parameters
    1,                    // Priority
    &telemetryTaskHandle, // Task handle
    0                     // Core ID 0
  );

  Serial.println("[SYSTEM] Telemetry worker running on Core 0. Sensor/LED loop running on Core 1.");
}

// =============================================================================
// MAIN LOOP (RUNS DEDICATED ON CORE 1 - FAST SENSOR & LED UPDATES)
// =============================================================================
void loop() {
  unsigned long current_ms = millis();

  // Poll all 3 ultrasonic sensors and update LEDs with minimal debounce
  for (int i = 0; i < NUM_SLOTS; i++) {
    int slot_id = BAYS[i].slot_id;

    // Small delay between ultrasonic pings to prevent acoustic reflection cross-talk
    delay(15);

    float distance = readUltrasonicDistance(BAYS[i].trig_pin, BAYS[i].echo_pin);
    String raw_status = evaluateRawStatus(distance);

    // Debouncing: Require 2 consecutive stable readings to change published state
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

      // Update physical LEDs immediately on verified state change
      updateBayLEDs(i, slot_states[i].published_status);

      Serial.printf("[LOCAL STATE CHANGE] Bay #%d: %s (%.1f cm) -> LEDs Updated in <200ms\n",
                    slot_id, slot_states[i].published_status.c_str(), distance);
    }

    // Ensure initial LED state is established on first reading
    if (slot_states[i].published_status != "Unknown") {
      updateBayLEDs(i, slot_states[i].published_status);
    }

    slot_states[i].last_distance_cm = distance;

    // Enqueue telemetry update if:
    // a) State changed (immediate transmission)
    // b) Periodic reporting interval elapsed (heartbeat telemetry)
    bool time_to_report = (current_ms - slot_states[i].last_sent_ms >= HEARTBEAT_INTERVAL_MS);

    if (state_changed || time_to_report) {
      enqueueTelemetry(slot_id, slot_states[i].published_status, distance);
      slot_states[i].last_sent_ms = current_ms;
    }
  }

  // Fast loop pacing delay (~40ms cycle rate)
  delay(30);
}
