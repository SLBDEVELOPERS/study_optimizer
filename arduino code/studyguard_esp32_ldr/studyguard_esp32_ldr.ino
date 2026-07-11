/*
  ╔══════════════════════════════════════════════════════════════╗
  ║        StudyGuard Pro — ESP32 Final Firmware (WiFi)          ║
  ║        HTTP Server + Sensor Auto-Control                      ║
  ╠══════════════════════════════════════════════════════════════╣
  ║  FIXES APPLIED:                                              ║
  ║  1. Non-blocking buzzer (millis state machine, no delay())   ║
  ║  2. tone() for passive buzzer — not digitalWrite HIGH/LOW    ║
  ║  3. Manual override flags — auto-control stops overwriting   ║
  ║  4. WiFi connect timeout (20s) — no infinite hang            ║
  ║  5. lampBrightness stores 0-255 PWM, pct separate variable   ║
  ║  6. buzzerState race condition fixed                         ║
  ║  7. startupBeep() non-blocking                               ║
  ╠══════════════════════════════════════════════════════════════╣
  ║  Libraries needed (Arduino IDE → Manage Libraries):          ║
  ║    • DHT sensor library  by Adafruit                         ║
  ║    • Adafruit Unified Sensor  by Adafruit                    ║
  ║    • ArduinoJson         by Benoit Blanchon                  ║
  ╠══════════════════════════════════════════════════════════════╣
  ║  Pin Map:                                                    ║
  ║    GPIO4   DHT22 DATA                                        ║
  ║    GPIO34  LDR (analog, voltage divider w/ 10k to GND)       ║
  ║    GPIO25  LED Lamp (MOSFET PWM)                             ║
  ║    GPIO26  Passive Buzzer (tone())                           ║
  ║    GPIO27  DC Fan (NPN transistor)                           ║
  ║    GPIO12  LED_TEMP                                          ║
  ║    GPIO13  LED_POSTURE                                       ║
  ║    GPIO14  LED_LIGHT                                         ║
  ║    GPIO32  LED_WIFI                                          ║
  ║    GPIO33  LED_FAN                                           ║
  ╚══════════════════════════════════════════════════════════════╝
*/

#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include "DHT.h"

// ── WiFi Credentials ──────────────────────────────────────────
const char* WIFI_SSID = "slb";
const char* WIFI_PASS = "12345678";

// ── Pins ──────────────────────────────────────────────────────
#define DHTPIN      4
#define DHTTYPE     DHT22
#define LDR_PIN     34    // Analog input, voltage divider w/ 10k to GND

#define LAMP_PIN    25    // MOSFET gate → LED lamp
#define BUZZER_PIN  26    // Passive buzzer (uses tone())
#define FAN_PIN     27    // NPN transistor base → DC fan

#define LED_TEMP    12
#define LED_POSTURE 13
#define LED_LIGHT   14
#define LED_WIFI    32
#define LED_FAN     33

// ── PWM (LED Lamp) ────────────────────────────────────────────
#define LAMP_CHANNEL  0
#define PWM_FREQ      5000
#define PWM_RES       8     // 0–255

// ── Auto-Control Thresholds ───────────────────────────────────
#define TEMP_FAN_ON    28.0f
#define TEMP_FAN_OFF   25.0f
// LDR raw ADC values (0-4095). These are rough starting points —
// recalibrate by watching Serial output in your actual room lighting.
// Lower value = darker (LDR resistance higher, divider pulls toward GND)
#define LUX_LOW        200
#define LUX_GOOD       300

// ── Buzzer Tones ──────────────────────────────────────────────
#define BUZZ_POSTURE_HZ   1500
#define BUZZ_DROWSY_HZ    1800
#define BUZZ_STARTUP_HZ1  1000
#define BUZZ_STARTUP_HZ2  1500
#define BUZZ_ON_MS        500
#define BUZZ_OFF_MS       200
#define BUZZ_STARTUP_MS   150

unsigned long lastSensorRead = 0;
const unsigned long sensorInterval = 1000;

// ── WiFi Connect Timeout ──────────────────────────────────────
#define WIFI_TIMEOUT_MS  20000   // FIX #4: 20s max wait

// ─────────────────────────────────────────────────────────────
// Objects
// ─────────────────────────────────────────────────────────────
DHT       dht(DHTPIN, DHTTYPE);
WebServer server(80);

// ─────────────────────────────────────────────────────────────
// Sensor State
// ─────────────────────────────────────────────────────────────
float temperature  = 0.0f;
float humidity     = 0.0f;
int   lux          = 0;    // raw LDR ADC reading, 0-4095

// ─────────────────────────────────────────────────────────────
// Actuator State
// ─────────────────────────────────────────────────────────────
bool  fanState         = false;
int   lampPWM          = 0;       // FIX #5: 0–255 PWM value
int   lampPct          = 0;       // FIX #5: 0–100 percentage (separate)

// FIX #3: Manual override flags
// When true, auto-control won't overwrite the manual setting
bool  manualFanOverride  = false;
bool  manualLampOverride = false;

// ─────────────────────────────────────────────────────────────
// Alert State
// ─────────────────────────────────────────────────────────────
bool postureAlert = false;
bool fatigueAlert = false;

// ─────────────────────────────────────────────────────────────
// Non-blocking Buzzer State Machine  (FIX #1 + FIX #2)
// ─────────────────────────────────────────────────────────────
enum BuzzMode { BUZZ_IDLE, BUZZ_POSTURE, BUZZ_DROWSY, BUZZ_STARTUP };

struct {
  BuzzMode      mode    = BUZZ_IDLE;
  int           step    = 0;
  int           totalSteps = 0;   // repeats × 2 (on+off pairs)
  unsigned long nextAt  = 0;
  int           hz      = 1500;
} buzzer;

bool isBuzzerActive() { return buzzer.mode != BUZZ_IDLE; }

void startBuzz(BuzzMode mode, int hz, int repeats) {
  buzzer.mode       = mode;
  buzzer.hz         = hz;
  buzzer.step       = 0;
  buzzer.totalSteps = repeats * 2;  // each repeat = 1 on + 1 off
  buzzer.nextAt     = millis();     // start immediately
}

// Called every loop() — no delay() anywhere  (FIX #1)
void handleBuzzer() {
  if (buzzer.mode == BUZZ_IDLE)    return;
  if (millis() < buzzer.nextAt)    return;

  bool isOn = (buzzer.step % 2 == 0);  // even=on, odd=off

  if (isOn) {
    tone(BUZZER_PIN, buzzer.hz);        // FIX #2: tone() not digitalWrite
    buzzer.nextAt = millis() + BUZZ_ON_MS;
  } else {
    noTone(BUZZER_PIN);
    buzzer.nextAt = millis() + BUZZ_OFF_MS;
  }

  buzzer.step++;

  if (buzzer.step >= buzzer.totalSteps) {
    noTone(BUZZER_PIN);
    buzzer.mode = BUZZ_IDLE;            // FIX #6: clean finish
  }
}

// Startup beep: 2× rising notes  (FIX #7: non-blocking)
void scheduleStartupBeep() {
  // Startup uses BUZZ_STARTUP mode with 2 different frequencies
  // We handle this as a special 4-step sequence in handleBuzzer
  buzzer.mode       = BUZZ_STARTUP;
  buzzer.step       = 0;
  buzzer.totalSteps = 4;  // on1, off1, on2, off2
  buzzer.nextAt     = millis();
}

void handleStartupBuzzer() {
  if (buzzer.mode != BUZZ_STARTUP)  return;
  if (millis() < buzzer.nextAt)     return;

  switch (buzzer.step) {
    case 0:
      tone(BUZZER_PIN, BUZZ_STARTUP_HZ1);
      buzzer.nextAt = millis() + BUZZ_STARTUP_MS;
      break;
    case 1:
      noTone(BUZZER_PIN);
      buzzer.nextAt = millis() + 80;
      break;
    case 2:
      tone(BUZZER_PIN, BUZZ_STARTUP_HZ2);
      buzzer.nextAt = millis() + BUZZ_STARTUP_MS;
      break;
    case 3:
      noTone(BUZZER_PIN);
      buzzer.mode = BUZZ_IDLE;
      break;
  }
  buzzer.step++;
}

// ─────────────────────────────────────────────────────────────
// Actuator Helpers
// ─────────────────────────────────────────────────────────────
void setFan(bool state) {
  fanState = state;
  digitalWrite(FAN_PIN, state ? HIGH : LOW);
  digitalWrite(LED_FAN, state ? HIGH : LOW);
}

void setLamp(int pwm_0_255) {
  lampPWM = constrain(pwm_0_255, 0, 255);
  lampPct = map(lampPWM, 0, 255, 0, 100);   // FIX #5: keep both
  ledcWrite(LAMP_PIN, lampPWM);
  digitalWrite(LED_LIGHT, lampPWM > 50 ? HIGH : LOW);
}

// ─────────────────────────────────────────────────────────────
// WiFi — with timeout  (FIX #4)
// ─────────────────────────────────────────────────────────────
void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting WiFi");

  unsigned long start = millis();

  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - start > WIFI_TIMEOUT_MS) {
      Serial.println("\nWiFi timeout! Running without WiFi.");
      digitalWrite(LED_WIFI, LOW);
      return;                          // FIX #4: don't hang forever
    }
    delay(500);
    Serial.print(".");
    digitalWrite(LED_WIFI, !digitalRead(LED_WIFI));
  }

  digitalWrite(LED_WIFI, HIGH);
  Serial.println();
  Serial.print("Connected! IP: ");
  Serial.println(WiFi.localIP());
}

// ─────────────────────────────────────────────────────────────
// HTTP Handlers
// ─────────────────────────────────────────────────────────────
void handlePing() {
  server.send(200, "application/json", "{\"status\":\"ok\"}");
}

void handleStatus() {
  StaticJsonDocument<512> doc;

  doc["temperature"]    = temperature;
  doc["humidity"]       = humidity;
  doc["lux"]            = lux;
  doc["fan"]            = fanState;
  doc["lamp_pwm"]       = lampPWM;       // FIX #5: raw PWM
  doc["lamp_pct"]       = lampPct;       // FIX #5: percentage
  doc["buzzer_active"]  = isBuzzerActive(); // FIX #6: real-time state
  doc["posture_alert"]  = postureAlert;
  doc["fatigue_alert"]  = fatigueAlert;
  doc["manual_fan"]     = manualFanOverride;
  doc["manual_lamp"]    = manualLampOverride;
  doc["wifi_rssi"]      = WiFi.RSSI();
  doc["ip"]             = WiFi.localIP().toString();

  String response;
  serializeJson(doc, response);
  server.send(200, "application/json", response);
}

void handleCommand() {
  if (!server.hasArg("plain")) {
    server.send(400, "application/json", "{\"error\":\"No body\"}");
    return;
  }

  StaticJsonDocument<512> doc;
  DeserializationError err = deserializeJson(doc, server.arg("plain"));

  if (err) {
    server.send(400, "application/json", "{\"error\":\"Invalid JSON\"}");
    return;
  }

  String action = doc["action"] | "";

  // ── Buzzer alert ────────────────────────────────────────
  if (action == "buzzer") {
    String pattern = doc["pattern"] | "posture";
    int    repeats = doc["repeats"] | 1;

    if (pattern == "posture") {
      postureAlert = true;
      digitalWrite(LED_POSTURE, HIGH);
      startBuzz(BUZZ_POSTURE, BUZZ_POSTURE_HZ, repeats);  // FIX #1: non-blocking

    } else if (pattern == "drowsy") {
      fatigueAlert = true;
      digitalWrite(LED_POSTURE, HIGH);
      startBuzz(BUZZ_DROWSY, BUZZ_DROWSY_HZ, repeats);    // FIX #1: non-blocking
    }
  }

  // ── Posture OK ──────────────────────────────────────────
  else if (action == "posture_ok") {
    postureAlert = false;
    fatigueAlert = false;
    digitalWrite(LED_POSTURE, LOW);
  }

  // ── Silent ──────────────────────────────────────────────
  else if (action == "silent") {
    noTone(BUZZER_PIN);
    buzzer.mode = BUZZ_IDLE;
  }

  // ── Fan manual ──────────────────────────────────────────
  else if (action == "fan") {
    bool state = doc["state"] | false;
    manualFanOverride = true;           // FIX #3: lock out auto-control
    setFan(state);
  }

  // ── Fan auto ────────────────────────────────────────────
  else if (action == "fan_auto") {
    manualFanOverride = false;          // FIX #3: release lock
    Serial.println("Fan: auto mode");
  }

  // ── Lamp manual ─────────────────────────────────────────
  else if (action == "lamp") {
    int brightness = doc["brightness"] | 0;
    manualLampOverride = true;          // FIX #3: lock out auto-control
    setLamp(brightness);
  }

  // ── Lamp auto ───────────────────────────────────────────
  else if (action == "lamp_auto") {
    manualLampOverride = false;         // FIX #3: release lock
    Serial.println("Lamp: auto mode");
  }

  // ── Reset all alerts ────────────────────────────────────
  else if (action == "reset_alerts") {
    postureAlert = false;
    fatigueAlert = false;
    noTone(BUZZER_PIN);
    buzzer.mode = BUZZ_IDLE;
    digitalWrite(LED_POSTURE, LOW);
  }

  server.send(200, "application/json", "{\"result\":\"ok\"}");
  // FIX #1: response sent immediately — buzzer runs in background
}

// ─────────────────────────────────────────────────────────────
// Sensor Reading
// ─────────────────────────────────────────────────────────────
void readSensors() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  int   l = analogRead(LDR_PIN);

  if (!isnan(t)) temperature = t;
  if (!isnan(h)) humidity    = h;
  lux = l;
}

// ─────────────────────────────────────────────────────────────
// Auto-Control  (FIX #3: checks override flags)
// ─────────────────────────────────────────────────────────────
void autoControlLamp() {
  if (manualLampOverride) return;     // FIX #3: skip if manual

  if      (lux < LUX_LOW)  setLamp(255);
  else if (lux < LUX_GOOD) setLamp(102);
  else                      setLamp(26);
}

void autoControlFan() {
  if (manualFanOverride) return;      // FIX #3: skip if manual

  if (temperature >= TEMP_FAN_ON  && !fanState) setFan(true);
  if (temperature <= TEMP_FAN_OFF &&  fanState) setFan(false);
}

// ─────────────────────────────────────────────────────────────
// Status LEDs
// ─────────────────────────────────────────────────────────────
void updateStatusLEDs() {
  digitalWrite(LED_TEMP, temperature >= TEMP_FAN_ON ? HIGH : LOW);
  digitalWrite(LED_WIFI, WiFi.status() == WL_CONNECTED ? HIGH : LOW);
  // LED_LIGHT and LED_FAN updated inside setLamp() / setFan()
  // LED_POSTURE updated in command handlers
}

// ─────────────────────────────────────────────────────────────
// Debug Print
// ─────────────────────────────────────────────────────────────
void printStatus() {
  Serial.printf(
    "T:%.1f°C  H:%.1f%%  Light:%d  Fan:%s  Lamp:%d%%  Posture:%s  Fatigue:%s\n",
    temperature, humidity, lux,
    fanState ? "ON" : "OFF",
    lampPct,
    postureAlert ? "ALERT" : "OK",
    fatigueAlert ? "ALERT" : "OK"
  );
}

// ─────────────────────────────────────────────────────────────
// HTTP Server Routes
// ─────────────────────────────────────────────────────────────
void setupServer() {
  server.on("/ping",    HTTP_GET,  handlePing);
  server.on("/status",  HTTP_GET,  handleStatus);
  server.on("/command", HTTP_POST, handleCommand);
  server.begin();
  Serial.println("HTTP Server started");
}

// ─────────────────────────────────────────────────────────────
//  SETUP
// ─────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);

  // Output pins
  pinMode(BUZZER_PIN,  OUTPUT);
  pinMode(FAN_PIN,     OUTPUT);
  pinMode(LED_POSTURE, OUTPUT);
  pinMode(LED_LIGHT,   OUTPUT);
  pinMode(LED_TEMP,    OUTPUT);
  pinMode(LED_FAN,     OUTPUT);
  pinMode(LED_WIFI,    OUTPUT);

  // Safe initial state
  digitalWrite(BUZZER_PIN,  LOW);
  digitalWrite(FAN_PIN,     LOW);
  digitalWrite(LED_POSTURE, LOW);
  digitalWrite(LED_LIGHT,   LOW);
  digitalWrite(LED_TEMP,    LOW);
  digitalWrite(LED_FAN,     LOW);
  digitalWrite(LED_WIFI,    LOW);

  // LDR — analog input, no special init needed
  pinMode(LDR_PIN, INPUT);
  dht.begin();

  // LED Lamp PWM
ledcAttach(LAMP_PIN, PWM_FREQ, PWM_RES);   // single call, no channel needed
ledcWrite(LAMP_PIN, 0);                     // use pin directly, not channel

  // WiFi — with timeout  (FIX #4)
  connectWiFi();
  if (WiFi.status() == WL_CONNECTED) {
    setupServer();
  }

  // FIX #7: Non-blocking startup beep
  scheduleStartupBeep();

  Serial.println("StudyGuard Pro Ready");
}

// ─────────────────────────────────────────────────────────────
//  LOOP
// ─────────────────────────────────────────────────────────────
void loop() {
  // HTTP client handling — always first, never blocked
  if (WiFi.status() == WL_CONNECTED) {
    server.handleClient();
  }

  // FIX #1 + FIX #7: Non-blocking buzzer state machine
  if (buzzer.mode == BUZZ_STARTUP) {
    handleStartupBuzzer();
  } else {
    handleBuzzer();
  }

  // Sensor read + auto-control every 1s
  if (millis() - lastSensorRead >= sensorInterval) {
    lastSensorRead = millis();

    readSensors();
    autoControlLamp();   // FIX #3: skipped if manual override
    autoControlFan();    // FIX #3: skipped if manual override
    updateStatusLEDs();
    printStatus();
  }
}

