#include <ArduinoJson.h>
#include <WebServer.h>
#include <WiFi.h>

const int BUZZER_PIN = 26;
const int FAN_PIN = 27;
const int LAMP_PIN = 25;

String wifiSsid = "YOUR_WIFI";
String wifiPassword = "YOUR_PASSWORD";
bool autoMode = true;
bool silentMode = false;
bool autoFan = true;
bool autoLamp = true;
int lampBrightness = 65;
bool fanOn = false;
float temperatureC = 30.0;
String deviceName = "ESP32 Desk Node";

WebServer server(80);

void buzzPattern(const String& pattern, int durationMs, int repeats) {
  if (silentMode) {
    return;
  }
  for (int i = 0; i < repeats; i++) {
    digitalWrite(BUZZER_PIN, HIGH);
    delay(durationMs);
    digitalWrite(BUZZER_PIN, LOW);
    delay(pattern == "drowsy" ? 250 : 120);
  }
}

void applyLampBrightness(int brightness) {
  lampBrightness = constrain(brightness, 0, 100);
  ledcWrite(0, map(lampBrightness, 0, 100, 0, 255));
}

void applyFan(bool enabled) {
  fanOn = enabled;
  digitalWrite(FAN_PIN, fanOn ? HIGH : LOW);
}

void sendJson(int code, JsonDocument& doc) {
  String response;
  serializeJson(doc, response);
  server.send(code, "application/json", response);
}

void handlePing() {
  StaticJsonDocument<96> doc;
  doc["ok"] = true;
  doc["device_name"] = deviceName;
  sendJson(200, doc);
}

void handleStatus() {
  StaticJsonDocument<256> doc;
  doc["device_name"] = deviceName;
  doc["fan_on"] = fanOn;
  doc["lamp_brightness"] = lampBrightness;
  doc["temperature_c"] = temperatureC;
  doc["auto_mode"] = autoMode;
  doc["silent_mode"] = silentMode;
  doc["wifi_ssid"] = wifiSsid;
  sendJson(200, doc);
}

void handleCommand() {
  StaticJsonDocument<384> payload;
  DeserializationError err = deserializeJson(payload, server.arg("plain"));
  if (err) {
    StaticJsonDocument<96> doc;
    doc["error"] = "invalid_json";
    sendJson(400, doc);
    return;
  }

  String action = payload["action"] | "";
  if (action == "buzzer") {
    String pattern = payload["pattern"] | "posture";
    int durationMs = payload["duration_ms"] | 500;
    int repeats = payload["repeats"] | 1;
    buzzPattern(pattern, durationMs, repeats);
  } else if (action == "fan") {
    applyFan(String(payload["state"] | "off") == "on");
  } else if (action == "lamp") {
    applyLampBrightness(payload["brightness"] | lampBrightness);
  }

  StaticJsonDocument<128> doc;
  doc["ok"] = true;
  doc["action"] = action;
  sendJson(200, doc);
}

void handleSettings() {
  StaticJsonDocument<512> payload;
  DeserializationError err = deserializeJson(payload, server.arg("plain"));
  if (err) {
    StaticJsonDocument<96> doc;
    doc["error"] = "invalid_json";
    sendJson(400, doc);
    return;
  }

  wifiSsid = payload["wifi_ssid"] | wifiSsid;
  wifiPassword = payload["wifi_password"] | wifiPassword;
  autoMode = payload["auto_mode"] | autoMode;
  silentMode = payload["silent_mode"] | silentMode;
  autoFan = payload["auto_fan"] | autoFan;
  autoLamp = payload["auto_lamp"] | autoLamp;

  StaticJsonDocument<192> doc;
  doc["ok"] = true;
  doc["auto_mode"] = autoMode;
  doc["silent_mode"] = silentMode;
  doc["wifi_ssid"] = wifiSsid;
  sendJson(200, doc);
}

void setup() {
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(FAN_PIN, OUTPUT);
  ledcSetup(0, 5000, 8);
  ledcAttachPin(LAMP_PIN, 0);

  Serial.begin(115200);
  WiFi.begin(wifiSsid.c_str(), wifiPassword.c_str());
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
  }

  server.on("/ping", HTTP_GET, handlePing);
  server.on("/status", HTTP_GET, handleStatus);
  server.on("/command", HTTP_POST, handleCommand);
  server.on("/settings", HTTP_POST, handleSettings);
  server.begin();
}

void loop() {
  server.handleClient();
}
