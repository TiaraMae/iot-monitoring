#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <vector>
#include <math.h>
#include <esp_adc_cal.h>
#include <Preferences.h>
#include <esp_task_wdt.h>

// =========================
// WIFI CONFIG
// =========================
const char* WIFI_SSID = "SGU-Guest";
const char* WIFI_PASS = "Springbloom@2026";

// =========================
// MQTT CONFIG
// =========================
const char* mqtt_server = "d57bf82836a7485d9b67b270c681fe6e.s1.eu.hivemq.cloud";
const int mqtt_port = 8883;
const char* mqtt_user = "esp32user";
const char* mqtt_pass = "IoTTHESIS1";

// =========================
// PORT PIN MAP
// =========================
#define PINBUTTON    1
#define PINBUTTON2   3
#define PINLED       4
#define PINSCTADC    0
#define PINDHT1      5
#define PINDHT2      6
#define PINDS18B20   7
#define PINI2CSDA    8
#define PINI2CSCL    9
#define PINBUZZER    10

#define DHTTYPE DHT22

DHT dht1(PINDHT1, DHTTYPE);
DHT dht2(PINDHT2, DHTTYPE);
OneWire oneWire(PINDS18B20);
DallasTemperature dsCoil(&oneWire);
Adafruit_BME280 bme;

esp_adc_cal_characteristics_t adc1_chars;

// =========================
// DATA BUFFER
// =========================
struct BufferedData {
  String payload;
  unsigned long timestamp;
};

std::vector<BufferedData> offlineQueue;
const int MAX_QUEUE_SIZE = 200;

// =========================
// SENSOR DATA
// =========================
struct SensorPair {
  float t1, h1, t2, h2, t3;
  bool valid;
};

// =========================
// MQTT / DEVICE
// =========================
WiFiClientSecure espClient;
PubSubClient client(espClient);
String deviceMac = "";

Preferences prefs;

// =========================
// DEVICE TYPE / FLOW STATE
// =========================
String applianceType = "unpaired";   // unpaired, HVAC, Dryer
bool isPaired = false;

float nodeCf = 0.0;        // Calibration factor from backend
float nodeDeductor = 0.0;  // Deductor from backend

bool calibrationAcked = false;
bool calibrationSavePending = false;

bool maintenanceRequestPending = false;

// =========================
// AVERAGING
// =========================
unsigned long lastSampleTime = 0;
const unsigned long SAMPLE_INTERVAL = 2000;
int sampleCount = 0;
const int MAX_SAMPLES = 5;

float sumDHT1T = 0, sumDHT1H = 0;
float sumDHT2T = 0, sumDHT2H = 0;
float sumDS18B20T = 0;
float sumBME280T = 0, sumBME280H = 0, sumBME280P = 0;
float sumCurrentA = 0.0;

// Per-metric valid sample counters (decoupled from sampleCount timing)
int validDHT1T = 0, validDHT1H = 0;
int validDHT2T = 0, validDHT2H = 0;
int validDS18B20T = 0;
int validCurrentA = 0;

// Last-known-good values for fallback when all samples in a window are invalid
float lastGoodDHT1T = 0, lastGoodDHT1H = 0;
float lastGoodDHT2T = 0, lastGoodDHT2H = 0;
float lastGoodDS18B20T = 0;

// DHT22 stuck-value detection (mirrors BME280 strategy)
int dht1StuckCounter = 0, dht2StuckCounter = 0;
unsigned long lastDhtResetMs = 0;
// Per-window average tracking for stuck detection (checked every 10s, not every 2s)
float prevWindowDHT1T = NAN, prevWindowDHT1H = NAN;
float prevWindowDHT2T = NAN, prevWindowDHT2H = NAN;
float currWindowDHT1T = NAN, currWindowDHT1H = NAN;
float currWindowDHT2T = NAN, currWindowDHT2H = NAN;

// =========================
// BME280
// =========================
uint8_t bmeAddress = 0x76;
int bmeValidSamples = 0;
int bmeNanCounter = 0;
int bmeStuckCounter = 0;
float lastBt = NAN, lastBh = NAN, lastBp = NAN;
int bmeOutOfRangeCounter = 0;
unsigned long lastBmeResetMs = 0;

// =========================
// CONNECTION STATUS
// =========================
unsigned long lastLedBlink = 0;
bool ledState = LOW;
bool ledBlinkPhase = false;
unsigned long lastWiFiRetry = 0;
unsigned long lastMqttRetry = 0;
unsigned long lastCheckinTime = 0;

// =========================
// LED STATE MACHINE
// =========================
unsigned long lastIdleBlink = 0;
float lastAvgCurrent = 0.0;  // For LED state machine

// =========================
// CALIBRATION STATE
// =========================
const unsigned long CALIB_TIMEOUT_MS = 10UL * 60UL * 1000UL;
const unsigned long CALIB_APPROVAL_TIMEOUT_MS = 10000UL;
const float CALIB_REQUIRED_DELTA_T = 8.0;

enum CalibState {
  CALIBIDLE = 0,
  CALIBWAITAPPROVAL,
  CALIBBASELINEWAIT,
  CALIBRUNNING
};

CalibState calibState = CALIBIDLE;
unsigned long calibStartedAt = 0;
unsigned long calibLastCheckAt = 0;
unsigned long calibApprovalRequestedAt = 0;

float calibBaseT1 = NAN, calibBaseH1 = NAN, calibBaseT2 = NAN, calibBaseH2 = NAN, calibBaseT3 = NAN;
float calibFinalT1 = NAN, calibFinalH1 = NAN, calibFinalT2 = NAN, calibFinalH2 = NAN, calibFinalT3 = NAN;
float calibDeltaT = NAN;

// =========================
// BUTTON LOGIC
// =========================
unsigned long btn1PressedAt = 0;
int btn1State = 0;  // 0 idle, 1 timing, 2 sent

unsigned long btn2PressedAt = 0;
int btn2State = 0;  // 0 idle, 1 timing, 2 sent

// =========================
// HELPERS
// =========================
String jnum(float v, int digits = 2) {
  if (isnan(v)) return "null";
  return String(v, digits);
}

void buzzerOn() {
  digitalWrite(PINBUZZER, HIGH);
}

void buzzerOff() {
  digitalWrite(PINBUZZER, LOW);
}

void beepShort(int count, int onMs = 120, int offMs = 120) {
  for (int i = 0; i < count; i++) {
    buzzerOn();
    delay(onMs);
    buzzerOff();
    if (i < count - 1) delay(offMs);
  }
}

void publishCheckin() {
  if (!client.connected()) return;
  String ev = "{\"mac\":\"" + deviceMac + "\",\"event\":\"checkin\"}";
  publishEventJson(ev);
  lastCheckinTime = millis();
  Serial.println("TX CHECKIN");
}

void beepLongFail(unsigned long ms = 1200) {
  buzzerOn();
  delay(ms);
  buzzerOff();
}

void beepLongFailTwice() {
  beepLongFail(800);
  delay(300);
  beepLongFail(800);
}

void beepRequest() {
  beepShort(1, 120, 0);
}

void beepConfirmed() {
  beepShort(2, 120, 100);
}

void beepSuccess() {
  beepShort(3, 120, 120);
}

String workflowLabel() {
  if (!isPaired) return "unpaired";
  if (calibState == CALIBWAITAPPROVAL) return "calib-requested";
  if (calibState == CALIBBASELINEWAIT || calibState == CALIBRUNNING) return "calibrating";
  if (calibrationSavePending) return "calib-saving";
  if (!calibrationAcked) return "need-calibration";
  if (maintenanceRequestPending) return "maintenance-requested";
  return "ready";
}

void printWorkflow() {
  Serial.print("PAIR TYPE: ");
  Serial.print(applianceType);
  Serial.print(" | FLOW: ");
  Serial.println(workflowLabel());
}

String addAgeToPayload(const String& payload, unsigned long ageMs) {
  String out = payload;
  if (out.endsWith("}")) {
    out.remove(out.length() - 1);
    out += ",\"ago\":0,\"agoms\":" + String(ageMs) + "}";
  }
  return out;
}

void publishEventJson(const String& json) {
  if (!client.connected()) {
    Serial.println("TX EVENT skipped: MQTT not connected");
    Serial.println(json);
    return;
  }

  String topic = "iot/nodes/" + deviceMac + "/events";
  Serial.print("TX EVENT -> ");
  Serial.println(topic);
  Serial.println(json);

  client.publish(topic.c_str(), json.c_str());
}

bool publishTelemetry(const String& payload) {
  if (!client.connected()) {
    Serial.println("TX TELEMETRY skipped: MQTT not connected");
    Serial.println(payload);
    return false;
  }

  String topic = "iot/nodes/" + deviceMac + "/telemetry";
  Serial.print("TX TELEMETRY -> ");
  Serial.println(topic);
  Serial.println(payload);

  bool ok = client.publish(topic.c_str(), payload.c_str());

  if (!ok) {
    Serial.println("TX TELEMETRY FAILED: publish returned false");
  }
  return ok;
}

unsigned long lastConfigRequestTime = 0;

void requestBackendConfig() {
  String ev = "{\"mac\":\"" + deviceMac + "\",\"event\":\"event_request_config\"}";
  publishEventJson(ev);
  lastConfigRequestTime = millis();
  Serial.println("Requested backend config.");
}

void resetRuntimeFlowForPair() {
  calibrationAcked = false;
  calibrationSavePending = false;
  maintenanceRequestPending = false;
  calibState = CALIBIDLE;
}

void applyApplianceType(const String& newType) {
  bool changed = (applianceType != newType);

  if (newType == "unpaired") {
    bool wasPaired = isPaired; // Remember if we were paired before

    applianceType = "unpaired";
    isPaired = false;
    resetRuntimeFlowForPair();

    // Clear persisted pairing state
    prefs.begin("nodecfg", false);
    prefs.remove("paired");
    prefs.remove("type");
    prefs.remove("cf");
    prefs.remove("deductor");
    prefs.end();

    if (wasPaired) {
      beepLongFail(1500); 
    }

    Serial.println("PAIR CLEARED -> unpaired");
    printWorkflow();
    return;
  }

  bool wasUnpaired = !isPaired;
  applianceType = newType;
  isPaired = true;

  if (wasUnpaired || changed) {
    resetRuntimeFlowForPair();
    // Dryers do not require calibration — allow maintenance immediately
    if (applianceType == "Dryer") {
      calibrationAcked = true;
    }
    beepShort(1, 100, 0); // 1 Beep for pairing success
    Serial.print("PAIR OK -> ");
    Serial.println(applianceType);
  } else {
    Serial.print("PAIR CONFIRMED AGAIN -> ");
    Serial.println(applianceType);
  }

  // Persist pairing state across reboots
  prefs.begin("nodecfg", false);
  prefs.putBool("paired", isPaired);
  prefs.putString("type", applianceType);
  prefs.end();

  printWorkflow();
}

// =========================
// WIFI / MQTT
// =========================
void setupWifi() {
  delay(100);
  Serial.print("Connecting to ");
  Serial.println(WIFI_SSID);

  WiFi.mode(WIFI_OFF);
  delay(100);
  WiFi.mode(WIFI_STA);
  WiFi.setTxPower(WIFI_POWER_8_5dBm);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    digitalWrite(PINLED, !digitalRead(PINLED));
    delay(500);
    Serial.print(".");
    attempts++;
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    deviceMac = WiFi.macAddress();
    Serial.print("WiFi connected. MAC: ");
    Serial.println(deviceMac);
  } else {
    Serial.println("WiFi failed on initial boot. Will retry in background...");
    deviceMac = WiFi.macAddress();
  }
}

void callback(char* topic, byte* payload, unsigned int length) {
    String message = "";
    message.reserve(length + 1);
    for (unsigned int i = 0; i < length; i++) {
        message += (char)payload[i];
    }
    
    // Add this to remove hidden newlines or spaces!
    message.trim(); 

    Serial.print("RX CONTROL <- ");
    Serial.println(topic);
    Serial.println(message);

    // Pair/type feedback
    if (message == "settype:hvac") {
        applyApplianceType("HVAC");
        return;
    }
    if (message == "settype:dryer") {
        applyApplianceType("Dryer");
        return;
    }
    if (message == "settype:unpaired") {
        applyApplianceType("unpaired");
        return;
    }

    // Calibration factor / deductor from backend
    if (message.startsWith("setcf:")) {
        String val = message.substring(6);
        nodeCf = val.toFloat();
        prefs.begin("nodecfg", false);
        prefs.putFloat("cf", nodeCf);
        prefs.end();
        Serial.print("RX CF="); Serial.println(nodeCf);
        return;
    }
    if (message.startsWith("setdeductor:")) {
        String val = message.substring(12);
        nodeDeductor = val.toFloat();
        prefs.begin("nodecfg", false);
        prefs.putFloat("deductor", nodeDeductor);
        prefs.end();
        Serial.print("RX DEDUCTOR="); Serial.println(nodeDeductor);
        return;
    }

    // --- STATE RESTORE HANDLERS ---
    if (message == "maintenancedenied") {
        Serial.println("MAINTENANCE DENIED -> status not ready");
        maintenanceRequestPending = false;
        printWorkflow();
        return;
    }
    if (message == "restore:calibrationneeded") {
        calibrationAcked = false;
        calibState = CALIBIDLE;
        Serial.println("RESTORE -> need calibration");
        printWorkflow();
        return;
    }
    if (message == "restore:normal") {
        calibrationAcked = true;
        calibState = CALIBIDLE;
        Serial.println("RESTORE -> normal (maintenance allowed)");
        publishCheckin();  // Immediately tell backend we're alive
        printWorkflow();
        return;
    }

    // Calibration
    if (message == "startcalibration") {
        Serial.println("BACKEND CONFIRMED -> calibration start");
        beepShort(1, 120, 0);
        calibState = CALIBBASELINEWAIT;
        calibStartedAt = millis();
        calibLastCheckAt = 0;
        calibApprovalRequestedAt = 0;
        calibrationSavePending = false;

        calibBaseT1 = calibBaseH1 = calibBaseT2 = calibBaseH2 = calibBaseT3 = NAN;
        calibFinalT1 = calibFinalH1 = calibFinalT2 = calibFinalH2 = calibFinalT3 = NAN;
        calibDeltaT = NAN;
        printWorkflow();
        return;
    }
    if (message == "calibrationfailack") {
        Serial.println("BACKEND FAIL -> calibration denied/failed");
        beepLongFailTwice();
        calibState = CALIBIDLE;
        calibApprovalRequestedAt = 0;
        calibrationSavePending = false;
        printWorkflow();
        return;
    }
    if (message == "calibrationsuccessack") {
        Serial.println("BACKEND SUCCESS -> calibration saved");
        beepSuccess();
        calibrationAcked = true;
        calibrationSavePending = false;
        calibState = CALIBIDLE;
        printWorkflow();
        return;
    }

    // Baseline configured (v2 manual input)
    if (message == "baseline:set") {
        Serial.println("BACKEND -> baseline configuration acknowledged");
        beepSuccess();
        printWorkflow();
        return;
    }

    // Maintenance
    if (message == "maintenanceack") {
        Serial.println("BACKEND SUCCESS -> maintenance logged");
        beepLongFail(1000);
        maintenanceRequestPending = false;
        printWorkflow();
        return;
    }

    // Busy / generic deny
    if (message == "actiondenied:busy") {
        if (calibState == CALIBBASELINEWAIT || calibState == CALIBRUNNING || calibrationSavePending) {
            Serial.println("BACKEND DENY -> Ignored (Device is already successfully busy)");
            return;
        }
        Serial.println("BACKEND DENY -> device busy");
        beepLongFail(900);
        maintenanceRequestPending = false;
        printWorkflow();
        return;
    }
}

static unsigned long lastConnBuzzer = 0;

void checkConnection() {
  unsigned long now = millis();

  // --- LED STATE MACHINE ---
  // Priority: calibration > baselining > connection down > running > idle
  if (calibState == CALIBBASELINEWAIT || calibState == CALIBRUNNING) {
    // Calibration: slow blink (2s)
    if (now - lastLedBlink >= 2000) {
      lastLedBlink = now;
      ledState = !ledState;
      digitalWrite(PINLED, ledState);
    }
  } else if (isRunningByCurrent(lastAvgCurrent)) {
    // Running: solid ON
    digitalWrite(PINLED, HIGH);
  } else if (WiFi.status() != WL_CONNECTED) {
    // WiFi down: fast blink (200ms)
    if (now - lastLedBlink >= 200) {
      lastLedBlink = now;
      ledState = !ledState;
      digitalWrite(PINLED, ledState);
    }
  } else if (!client.connected()) {
    // MQTT down: medium blink (500ms)
    if (now - lastLedBlink >= 500) {
      lastLedBlink = now;
      ledState = !ledState;
      digitalWrite(PINLED, ledState);
    }
  } else if (isRunningByCurrent(lastAvgCurrent)) {
    // Running: solid LED
    digitalWrite(PINLED, HIGH);
  } else {
    // Idle: brief blink every 10s
    if (now - lastIdleBlink >= 10000) {
      lastIdleBlink = now;
      digitalWrite(PINLED, HIGH);
      delay(50);
      digitalWrite(PINLED, LOW);
    }
  }

  // --- CONNECTION DOWN BUZZER (every 10s) ---
  if ((WiFi.status() != WL_CONNECTED || !client.connected()) &&
      (calibState == CALIBIDLE)) {
    if (now - lastConnBuzzer >= 10000) {
      lastConnBuzzer = now;
      beepShort(1, 120, 0);
    }
  }

  // WiFi down
  if (WiFi.status() != WL_CONNECTED) {
    if (now - lastWiFiRetry >= 10000) {
      lastWiFiRetry = now;
      Serial.println("WiFi lost! Trying reconnect...");
      WiFi.disconnect();
      WiFi.mode(WIFI_OFF);
      delay(100);
      WiFi.mode(WIFI_STA);
      WiFi.setTxPower(WIFI_POWER_8_5dBm);
      WiFi.begin(WIFI_SSID, WIFI_PASS);
    }
    return;
  }

  // MQTT down
  if (!client.connected()) {
    if (now - lastMqttRetry >= 5000) {
      lastMqttRetry = now;

      String clientId = "ESP32-" + deviceMac;
      Serial.print("WiFi OK. Connecting MQTT... ");

      if (client.connect(clientId.c_str(), mqtt_user, mqtt_pass)) {
        Serial.println("connected");
        client.subscribe(("iot/nodes/" + deviceMac + "/control").c_str());
        digitalWrite(PINLED, HIGH);
        requestBackendConfig();
      } else {
        Serial.print("failed rc=");
        Serial.println(client.state());
      }
    }
    return;
  }

  // Retry config request if still unpaired
  if (applianceType == "unpaired" && now - lastConfigRequestTime >= 10000) {
    requestBackendConfig();
  }
}

// =========================
// SENSOR READS
// =========================
float readCurrentIrms() {
  if (nodeCf <= 0.0 || nodeDeductor < 0.0) {
    Serial.println("CURRENT READ SKIPPED: CF/deductor not set");
    return 0.0;
  }

  unsigned long startMillis = millis();
  long sum = 0;
  double sumSquared = 0;
  int count = 0;

  while (millis() - startMillis < 200) {
    if (count % 25 == 0) {
      delay(1);
      esp_task_wdt_reset();
    }
    long raw = analogRead(PINSCTADC);
    sum += raw;
    sumSquared += (double)raw * (double)raw;
    count++;
  }

  if (count == 0) return 0.0;

  float mean = (float)sum / count;
  float meanSquare = (float)(sumSquared / count);
  float variance = meanSquare - (mean * mean);
  if (variance < 0) variance = 0;

  float rmsADC = sqrt(variance);
  uint32_t trueVoltageMv = esp_adc_cal_raw_to_voltage((uint32_t)rmsADC, &adc1_chars);

  float rawAmps = (trueVoltageMv / 1000.0) * nodeCf;
  float finalAmps = rawAmps - nodeDeductor;
  if (finalAmps < 0) finalAmps = 0.0;

  return finalAmps;
}

bool isRunningByCurrent(float currentA) {
  return currentA >= 0.25;
}

SensorPair readHvacSensors() {
  SensorPair r;
  r.t1 = dht1.readTemperature();
  r.h1 = dht1.readHumidity();
  r.t2 = dht2.readTemperature();
  r.h2 = dht2.readHumidity();
  dsCoil.requestTemperatures();
  r.t3 = dsCoil.getTempCByIndex(0);

  if (r.t3 < -50 || r.t3 > 120) r.t3 = NAN;
  r.valid = !isnan(r.t1) && !isnan(r.h1) && !isnan(r.t2) && !isnan(r.h2) && !isnan(r.t3);
  return r;
}

// =========================
// CALIBRATION STATE MACHINE
// =========================
void finishCalibrationSuccess(unsigned long nowMs) {
  calibState = CALIBIDLE;
  calibrationSavePending = true;

  String ev =
    "{"
      "\"mac\":\"" + deviceMac + "\","
      "\"event\":\"calibration_success_request\","
      "\"elapsedms\":" + String(nowMs - calibStartedAt) + ","
      "\"deltaT\":" + jnum(calibDeltaT, 2) + ","
      "\"base\":{"
        "\"t1\":" + jnum(calibBaseT1, 2) + ","
        "\"h1\":" + jnum(calibBaseH1, 2) + ","
        "\"t2\":" + jnum(calibBaseT2, 2) + ","
        "\"h2\":" + jnum(calibBaseH2, 2) + ","
        "\"t3\":" + jnum(calibBaseT3, 2) +
      "},"
      "\"final\":{"
        "\"t1\":" + jnum(calibFinalT1, 2) + ","
        "\"h1\":" + jnum(calibFinalH1, 2) + ","
        "\"t2\":" + jnum(calibFinalT2, 2) + ","
        "\"h2\":" + jnum(calibFinalH2, 2) + ","
        "\"t3\":" + jnum(calibFinalT3, 2) +
      "}"
    "}";

  publishEventJson(ev);
  Serial.println("CALIBRATION DATA SENT -> waiting backend save ack...");
  printWorkflow();
}

void finishCalibrationFail(unsigned long nowMs, const String& reason) {
  calibState = CALIBIDLE;
  calibrationSavePending = false;

  String ev =
    "{"
      "\"mac\":\"" + deviceMac + "\","
      "\"event\":\"calibration_fail_request\","
      "\"elapsedms\":" + String(nowMs - calibStartedAt) + ","
      "\"reason\":\"" + reason + "\""
    "}";

  publishEventJson(ev);
  Serial.print("CALIBRATION FAIL REQUEST SENT -> ");
  Serial.println(reason);
  printWorkflow();
}

void handleCalibrationState() {
  if (calibState == CALIBIDLE) return;

  unsigned long now = millis();

  if (calibState == CALIBWAITAPPROVAL) {
    if (now - calibApprovalRequestedAt >= CALIB_APPROVAL_TIMEOUT_MS) {
      Serial.println("Calibration approval timeout.");
      beepLongFail(1000);
      calibState = CALIBIDLE;
      calibApprovalRequestedAt = 0;
      printWorkflow();
    }
    return;
  }

  if (now - calibStartedAt >= CALIB_TIMEOUT_MS) {
    finishCalibrationFail(now, "timeout10min");
    return;
  }

  if (calibLastCheckAt != 0 && now - calibLastCheckAt < 2200) return;
  calibLastCheckAt = now;

  SensorPair p = readHvacSensors();
  if (!p.valid) {
    Serial.println("CALIBRATION waiting: invalid HVAC sensor set.");
    return;
  }

  if (calibState == CALIBBASELINEWAIT) {
    calibBaseT1 = p.t1;
    calibBaseH1 = p.h1;
    calibBaseT2 = p.t2;
    calibBaseH2 = p.h2;
    calibBaseT3 = p.t3;
    calibState = CALIBRUNNING;

    Serial.print("Calibration baseline set. T1=");
    Serial.print(calibBaseT1, 2);
    Serial.print(" T2=");
    Serial.print(calibBaseT2, 2);
    Serial.print(" T3=");
    Serial.println(calibBaseT3, 2);

    String calibEv =
      "{"
        "\"mac\":\"" + deviceMac + "\","
        "\"event\":\"calibration_progress\","
        "\"t3\":" + jnum(calibBaseT3, 2) + ","
        "\"base_t3\":" + jnum(calibBaseT3, 2) + ","
        "\"delta\":0.00"
      "}";
    publishEventJson(calibEv);
    return;
  }

  calibDeltaT = fabs(p.t3 - calibBaseT3);

  Serial.print("Calibration running. T3=");
  Serial.print(p.t3, 2);
  Serial.print(" delta=");
  Serial.print(calibDeltaT, 2);
  Serial.print(" need=");
  Serial.println(CALIB_REQUIRED_DELTA_T, 2);

  String calibEv =
    "{"
      "\"mac\":\"" + deviceMac + "\","
      "\"event\":\"calibration_progress\","
      "\"t3\":" + jnum(p.t3, 2) + ","
      "\"base_t3\":" + jnum(calibBaseT3, 2) + ","
      "\"delta\":" + jnum(calibDeltaT, 2) +
    "}";
  publishEventJson(calibEv);

  if (calibDeltaT >= CALIB_REQUIRED_DELTA_T) {
    calibFinalT1 = p.t1;
    calibFinalH1 = p.h1;
    calibFinalT2 = p.t2;
    calibFinalH2 = p.h2;
    calibFinalT3 = p.t3;
    finishCalibrationSuccess(now);
  }
}

// =========================
// BUTTONS
// =========================
void handleButtons() {
  if (calibState == CALIBBASELINEWAIT || calibState == CALIBRUNNING || calibrationSavePending) {
    btn1State = 0;
    btn2State = 0;
    return;
  }

  unsigned long now = millis();
  bool b1 = (digitalRead(PINBUTTON) == LOW);
  bool b2 = (digitalRead(PINBUTTON2) == LOW);

  // ---------- BUTTON 1: MAINTENANCE ----------
  if (b1) {
    if (btn1State == 0) {
      btn1PressedAt = now;
      btn1State = 1;
    } else if (btn1State == 1 && now - btn1PressedAt >= 2000) {
      btn1State = 2;

      if (!isPaired) {
        Serial.println("BUTTON 1 denied: node not paired yet.");
        beepLongFail(700);
      } else if (!calibrationAcked) {
        Serial.println("BUTTON 1 denied: finish calibration first.");
        beepLongFail(700);
      } else if (maintenanceRequestPending || calibState != CALIBIDLE) {
        // SILENTLY ignore
      } else {
        maintenanceRequestPending = true;
        String ev = "{\"mac\":\"" + deviceMac + "\",\"event\":\"maintenance_request\"}";
        Serial.println("BUTTON 1 -> maintenance request sent");
        beepRequest();
        publishEventJson(ev);
        printWorkflow();
      }
    }
  } else {
    btn1State = 0;
  }

  // ---------- BUTTON 2: CALIBRATION (5s) — HVAC ONLY ----------
  if (b2) {
    if (btn2State == 0) {
      btn2PressedAt = now;
      btn2State = 1;
    } else if (btn2State == 1 && now - btn2PressedAt >= 5000) {
      // 5-second threshold reached — send calibration request (HVAC only)
      btn2State = 2;
      Serial.println("BUTTON 2 -> 5s threshold reached (calibration)");
      
      if (!isPaired) {
        Serial.println("BUTTON 2 denied: node not paired yet.");
        beepLongFail(700);
      } else if (applianceType == "Dryer") {
        Serial.println("BUTTON 2 denied: Dryer does not require calibration.");
        beepLongFail(700);
      } else if (calibrationSavePending || maintenanceRequestPending || calibState == CALIBWAITAPPROVAL) {
        // SILENTLY ignore
      } else {
        String ev = "{\"mac\":\"" + deviceMac + "\",\"event\":\"event_button2_calibration_request\"}";
        Serial.println("BUTTON 2 -> calibration request sent");
        publishEventJson(ev);
        printWorkflow();
      }
    }
  } else {
    // Button released before 5s — do nothing
    btn2State = 0;
  }
}

// =========================
// PAYLOAD BUILD
// =========================
String buildTelemetryPayload() {
  String payload = "{";
  payload += "\"mac\":\"" + deviceMac + "\",";
  payload += "\"type\":\"" + applianceType + "\",";
  payload += "\"calstate\":\"" + workflowLabel() + "\",";

  if (applianceType == "Dryer") {
    if (bmeValidSamples > 0) {
      payload += "\"BME280Temp\":" + jnum(sumBME280T / bmeValidSamples, 1) + ",";
      payload += "\"BME280Hum\":" + jnum(sumBME280H / bmeValidSamples, 1) + ",";
      payload += "\"BME280Pres\":" + jnum(sumBME280P / bmeValidSamples, 2) + ",";
    } else {
      payload += "\"BME280Temp\":null,";
      payload += "\"BME280Hum\":null,";
      payload += "\"BME280Pres\":null,";
    }
  } else {
    float avgDHT1T = validDHT1T > 0 ? sumDHT1T / validDHT1T : lastGoodDHT1T;
    float avgDHT1H = validDHT1H > 0 ? sumDHT1H / validDHT1H : lastGoodDHT1H;
    float avgDHT2T = validDHT2T > 0 ? sumDHT2T / validDHT2T : lastGoodDHT2T;
    float avgDHT2H = validDHT2H > 0 ? sumDHT2H / validDHT2H : lastGoodDHT2H;
    float avgDS18B20T = validDS18B20T > 0 ? sumDS18B20T / validDS18B20T : lastGoodDS18B20T;

    // Store averages for per-window stuck detection
    currWindowDHT1T = avgDHT1T;
    currWindowDHT1H = avgDHT1H;
    currWindowDHT2T = avgDHT2T;
    currWindowDHT2H = avgDHT2H;

    payload += "\"DHT1Temp\":" + jnum(avgDHT1T, 1) + ",";
    payload += "\"DHT1Hum\":" + jnum(avgDHT1H, 1) + ",";
    payload += "\"DHT2Temp\":" + jnum(avgDHT2T, 1) + ",";
    payload += "\"DHT2Hum\":" + jnum(avgDHT2H, 1) + ",";
    payload += "\"DS18B20Temp\":" + jnum(avgDS18B20T, 1) + ",";

    // Update last-known-good values for next window's fallback
    // Protect from poisoning by stuck values — only update if sensor is not flagged stuck
    if (validDHT1T > 0 && dht1StuckCounter < 3) lastGoodDHT1T = avgDHT1T;
    if (validDHT1H > 0 && dht1StuckCounter < 3) lastGoodDHT1H = avgDHT1H;
    if (validDHT2T > 0 && dht2StuckCounter < 3) lastGoodDHT2T = avgDHT2T;
    if (validDHT2H > 0 && dht2StuckCounter < 3) lastGoodDHT2H = avgDHT2H;
    if (validDS18B20T > 0) lastGoodDS18B20T = avgDS18B20T;
  }

  float avgCurrentA = validCurrentA > 0 ? sumCurrentA / validCurrentA : 0.0;
  payload += "\"CurrentA\":" + String(avgCurrentA, 3);
  // Always include running/idle status based on computed current
  payload += ",\"status\":\"" + String(isRunningByCurrent(avgCurrentA) ? "running" : "idle") + "\"";
  payload += "}";

  return payload;
}

void resetAverages() {
  sampleCount = 0;
  bmeValidSamples = 0;

  sumDHT1T = sumDHT1H = 0;
  sumDHT2T = sumDHT2H = 0;
  sumDS18B20T = 0;
  sumBME280T = sumBME280H = sumBME280P = 0;
  sumCurrentA = 0.0;

  validDHT1T = validDHT1H = 0;
  validDHT2T = validDHT2H = 0;
  validDS18B20T = 0;
  validCurrentA = 0;
}

// =========================
// SETUP / LOOP
// =========================
void setup() {
  Serial.begin(115200);
  delay(3000);
  Serial.println("Booting Sensor Node...");

  // Restore pairing state from NVS (survives power cycles)
  prefs.begin("nodecfg", true);
  bool savedPaired = prefs.getBool("paired", false);
  String savedType = prefs.getString("type", "unpaired");
  float savedCf = prefs.getFloat("cf", 0.0);
  float savedDeductor = prefs.getFloat("deductor", 0.0);
  prefs.end();
  if (savedPaired && (savedType == "HVAC" || savedType == "Dryer")) {
    isPaired = true;
    applianceType = savedType;
    Serial.print("RESTORED FROM NVS -> ");
    Serial.println(applianceType);
  }
  if (savedCf > 0.0 && savedDeductor >= 0.0) {
    nodeCf = savedCf;
    nodeDeductor = savedDeductor;
    Serial.print("RESTORED CF="); Serial.print(nodeCf);
    Serial.print(" DEDUCTOR="); Serial.println(nodeDeductor);
  }

  pinMode(PINBUTTON, INPUT_PULLUP);
  pinMode(PINBUTTON2, INPUT_PULLUP);
  pinMode(PINLED, OUTPUT);
  pinMode(PINBUZZER, OUTPUT);

  buzzerOff();
  digitalWrite(PINLED, LOW);

  analogReadResolution(12);
  analogSetPinAttenuation(PINSCTADC, ADC_11db);
  esp_adc_cal_characterize(ADC_UNIT_1, ADC_ATTEN_DB_11, ADC_WIDTH_BIT_12, 1100, &adc1_chars);

  // Connect WiFi first so backend sees us quickly
  setupWifi();

  dht1.begin();
  dht2.begin();
  dsCoil.begin();
  dsCoil.setWaitForConversion(false);

  Wire.begin(PINI2CSDA, PINI2CSCL);
  Wire.setClock(100000L);
  if (!bme.begin(0x76, &Wire)) {
    Serial.println("❌ BME280 not found at 0x76");
  } else {
    Serial.println("✅ BME280 OK");
    delay(100);
    bme.setSampling(Adafruit_BME280::MODE_NORMAL,
                    Adafruit_BME280::SAMPLING_X2,
                    Adafruit_BME280::SAMPLING_X16,
                    Adafruit_BME280::SAMPLING_X1,
                    Adafruit_BME280::FILTER_X16,
                    Adafruit_BME280::STANDBY_MS_62_5);
    delay(50);
  }

  dsCoil.requestTemperatures();
  (void)dsCoil.getTempCByIndex(0);

  // Warm-up DHT only for HVAC (Dryer doesn't use them)
  if (applianceType == "HVAC") {
    for (int i = 0; i < 10; i++) {
      float t1 = dht1.readTemperature();
      float t2 = dht2.readTemperature();
      if (!isnan(t1) && !isnan(t2)) break;
      delay(2000);
    }
  }

  espClient.setInsecure();
  espClient.setTimeout(20);

  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(callback);
  client.setBufferSize(1024);
  client.setKeepAlive(15);

  Serial.println("System Ready.");
}

void loop() {
  esp_task_wdt_reset();
  checkConnection();

  if (client.connected()) {
    client.loop();
    delay(1);
  }

  handleButtons();
  handleCalibrationState();

  unsigned long now = millis();

  if (now - lastSampleTime >= SAMPLE_INTERVAL &&
      calibState != CALIBBASELINEWAIT &&
      calibState != CALIBRUNNING) {

    lastSampleTime = now;

    if (applianceType == "Dryer") {
      auto softResetBME280 = [&]() {
        Serial.println("BME280 soft reset");
        Wire.beginTransmission(bmeAddress);
        Wire.write(0xE0);
        Wire.write(0xB6);
        Wire.endTransmission();
        delay(50);
        bme.begin(bmeAddress, &Wire);
        delay(100);
        bme.setSampling(Adafruit_BME280::MODE_NORMAL,
                        Adafruit_BME280::SAMPLING_X2,
                        Adafruit_BME280::SAMPLING_X16,
                        Adafruit_BME280::SAMPLING_X1,
                        Adafruit_BME280::FILTER_X16,
                        Adafruit_BME280::STANDBY_MS_62_5);
        delay(50);
        bmeNanCounter = 0;
        bmeStuckCounter = 0;
        bmeOutOfRangeCounter = 0;
        lastBt = NAN;
        lastBh = NAN;
        lastBp = NAN;
        lastBmeResetMs = millis();
      };

      // Soft-reset BME280 if it has been returning NaN for 5 consecutive samples
      if (bmeNanCounter >= 5) {
        if (millis() - lastBmeResetMs >= 5000) {
          Serial.println("BME280 NaN x5 -> soft reset");
          softResetBME280();
        } else {
          Serial.println("BME280 NaN x5 -> reset skipped (cooldown)");
        }
      }

      float bt = NAN, bh = NAN, bp = NAN;
      for (int attempt = 0; attempt < 3; attempt++) {
        bt = bme.readTemperature();
        delay(50);
        bh = bme.readHumidity();
        delay(50);
        bp = bme.readPressure() / 100.0F;
        if (!isnan(bt) && !isnan(bh) && !isnan(bp)) break;
        delay(50);
      }

      if (!isnan(bt) && !isnan(bh) && !isnan(bp)) {
        // Stuck-value detection — only when running (idle readings are naturally stable)
        if (isRunningByCurrent(lastAvgCurrent)) {
          if (bt == lastBt && bh == lastBh && bp == lastBp) {
            bmeStuckCounter++;
            Serial.printf("BME280 stuck (%d/15): T=%.1f H=%.1f P=%.1f\n",
                          bmeStuckCounter, bt, bh, bp);
            if (bmeStuckCounter >= 15) {
              if (millis() - lastBmeResetMs >= 5000) {
                Serial.println("BME280 stuck x15 -> soft reset");
                softResetBME280();
              } else {
                Serial.println("BME280 stuck x15 -> reset skipped (cooldown)");
              }
            }
          } else {
            bmeStuckCounter = 0;
          }
        } else {
          bmeStuckCounter = 0;  // reset counter when idle
        }

        // Out-of-range / corrupted detection
        if (bt > 85.0 || bp < 800.0 || bt < -40.0 || bp > 1100.0) {
          bmeOutOfRangeCounter++;
          Serial.printf("BME280 out of range (%d/15): T=%.1f H=%.1f P=%.1f\n",
                        bmeOutOfRangeCounter, bt, bh, bp);
          if (bmeOutOfRangeCounter >= 15) {
            if (millis() - lastBmeResetMs >= 5000) {
              Serial.println("BME280 out of range x15 -> soft reset");
              softResetBME280();
            } else {
              Serial.println("BME280 out of range x15 -> reset skipped (cooldown)");
            }
            bmeOutOfRangeCounter = 0;
          }
        } else {
          bmeOutOfRangeCounter = 0;
        }

        sumBME280T += bt;
        sumBME280H += bh;
        sumBME280P += bp;
        bmeValidSamples++;
        bmeNanCounter = 0;
        lastBt = bt;
        lastBh = bh;
        lastBp = bp;
      } else {
        bmeNanCounter++;
      }
    } else {
      float d1t = dht1.readTemperature();
      float d1h = dht1.readHumidity();
      delay(1);
      float d2t = dht2.readTemperature();
      float d2h = dht2.readHumidity();
      delay(1);
      dsCoil.requestTemperatures();
      unsigned long dsWaitStart = millis();
      while (millis() - dsWaitStart < 750) {
        delay(1);
        esp_task_wdt_reset();
      }
      float dst = dsCoil.getTempCByIndex(0);

      if (!isnan(d1t)) { sumDHT1T += d1t; validDHT1T++; }
      if (!isnan(d1h)) { sumDHT1H += d1h; validDHT1H++; }
      if (!isnan(d2t)) { sumDHT2T += d2t; validDHT2T++; }
      if (!isnan(d2h)) { sumDHT2H += d2h; validDHT2H++; }
      if (!isnan(dst) && dst > -50 && dst < 120) { sumDS18B20T += dst; validDS18B20T++; }
    }

    float currentVal = readCurrentIrms();
    lastAvgCurrent = currentVal;  // Track instantaneous state for LED
    sumCurrentA += currentVal;
    validCurrentA++;

    sampleCount++;
  }

  if (sampleCount >= MAX_SAMPLES) {
    // Send telemetry continuously when calibration is done (running or idle)
    if (calibrationAcked) {
      String basePayload = buildTelemetryPayload();
      resetAverages();

      // DHT stuck-value detection — check once per averaged window (every 10s)
      auto softResetDHT = [&]() {
        Serial.println("DHT soft reset (re-initializing)");
        dht1.begin();
        dht2.begin();
        delay(100);
        dht1StuckCounter = 0;
        dht2StuckCounter = 0;
        prevWindowDHT1T = prevWindowDHT1H = prevWindowDHT2T = prevWindowDHT2H = NAN;
        lastDhtResetMs = millis();
      };

      if (isRunningByCurrent(lastAvgCurrent)) {
        if (!isnan(currWindowDHT1T) && !isnan(currWindowDHT1H) &&
            !isnan(prevWindowDHT1T) && !isnan(prevWindowDHT1H) &&
            fabs(currWindowDHT1T - prevWindowDHT1T) < 0.05 &&
            fabs(currWindowDHT1H - prevWindowDHT1H) < 0.05) {
          dht1StuckCounter++;
          Serial.printf("DHT1 stuck (%d/3): T=%.1f H=%.1f\n", dht1StuckCounter, currWindowDHT1T, currWindowDHT1H);
          if (dht1StuckCounter >= 3 && millis() - lastDhtResetMs >= 5000) {
            softResetDHT();
          }
        } else {
          dht1StuckCounter = 0;
        }

        if (!isnan(currWindowDHT2T) && !isnan(currWindowDHT2H) &&
            !isnan(prevWindowDHT2T) && !isnan(prevWindowDHT2H) &&
            fabs(currWindowDHT2T - prevWindowDHT2T) < 0.05 &&
            fabs(currWindowDHT2H - prevWindowDHT2H) < 0.05) {
          dht2StuckCounter++;
          Serial.printf("DHT2 stuck (%d/3): T=%.1f H=%.1f\n", dht2StuckCounter, currWindowDHT2T, currWindowDHT2H);
          if (dht2StuckCounter >= 3 && millis() - lastDhtResetMs >= 5000) {
            softResetDHT();
          }
        } else {
          dht2StuckCounter = 0;
        }
      } else {
        dht1StuckCounter = 0;
        dht2StuckCounter = 0;
      }

      prevWindowDHT1T = currWindowDHT1T;
      prevWindowDHT1H = currWindowDHT1H;
      prevWindowDHT2T = currWindowDHT2T;
      prevWindowDHT2H = currWindowDHT2H;

      // Try to publish current payload; buffer it if publish fails or MQTT is down
      bool published = false;
      if (client.connected()) {
        published = publishTelemetry(addAgeToPayload(basePayload, 0));
      }
      if (!published) {
        if ((int)offlineQueue.size() >= MAX_QUEUE_SIZE) {
          offlineQueue.erase(offlineQueue.begin());
        }
        offlineQueue.push_back({basePayload, millis()});
        Serial.println("[BUFFER] Queued 1 reading (total: " + String(offlineQueue.size()) + ")");
      }

      // Flush buffered readings with stop-on-failure protection
      if (!offlineQueue.empty() && client.connected()) {
        Serial.println("[BUFFER] Flushing " + String(offlineQueue.size()) + " buffered readings");
        size_t i = 0;
        size_t flushed = 0;
        const size_t MAX_FLUSH_PER_LOOP = 10;
        while (i < offlineQueue.size() && flushed < MAX_FLUSH_PER_LOOP) {
          BufferedData& item = offlineQueue[i];
          unsigned long ageMs = millis() - item.timestamp;
          bool ok = publishTelemetry(addAgeToPayload(item.payload, ageMs));
          if (ok) {
            offlineQueue.erase(offlineQueue.begin() + i);
            flushed++;
            delay(120);
          } else {
            Serial.println("[BUFFER] Flush halted at item " + String(i) + "/" + String(offlineQueue.size()) + " (publish failed)");
            break;
          }
        }
        Serial.println("[BUFFER] Flushed " + String(flushed) + "/" + String(flushed + offlineQueue.size()) + " readings");
      }
    } else {
      // Not calibrated yet: discard samples
      resetAverages();
    }
  }

  // Periodic checkin when idle so backend doesn't mark us offline
  const unsigned long CHECKIN_INTERVAL_MS = 600000UL;  // 10 minutes
  if (client.connected() && !isRunningByCurrent(lastAvgCurrent) && (millis() - lastCheckinTime >= CHECKIN_INTERVAL_MS)) {
    publishCheckin();
  }
}