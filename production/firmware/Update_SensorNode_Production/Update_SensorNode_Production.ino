#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
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

// WiFi + MQTT credentials live in secrets.h (gitignored).
// Copy secrets.h.example to secrets.h and fill in your own values.
#include "secrets.h"

// ISRG Root X1 CA Certificate (Let's Encrypt / HiveMQ Cloud)
// Required for WiFiClientSecure to validate the broker TLS certificate
// Source: https://letsencrypt.org/certs/isrgrootx1.pem
const char* ca_cert =
"-----BEGIN CERTIFICATE-----\n"
"MIIFazCCA1OgAwIBAgIRAIIQz7DSQONZRGPgu2OCiwAwDQYJKoZIhvcNAQELBQAw\n"
"TzELMAkGA1UEBhMCVVMxKTAnBgNVBAoTIEludGVybmV0IFNlY3VyaXR5IFJlc2Vh\n"
"cmNoIEdyb3VwMRUwEwYDVQQDEwxJU1JHIFJvb3QgWDEwHhcNMTUwNjA0MTEwNDM4\n"
"WhcNMzUwNjA0MTEwNDM4WjBPMQswCQYDVQQGEwJVUzEpMCcGA1UEChMgSW50ZXJu\n"
"ZXQgU2VjdXJpdHkgUmVzZWFyY2ggR3JvdXAxFTATBgNVBAMTDElTUkcgUm9vdCBY\n"
"MTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAK3oJHP0FDfzm54rVygc\n"
"h77ct984kIxuPOZXoHj3dcKi/vVqbvYATyjb3miGbESTtrFj/RQSa78f0uoxmyF+\n"
"0TM8ukj13Xnfs7j/EvEhmkvBioZxaUpmZmyPfjxwv60pIgbz5MDmgK7iS4+3mX6U\n"
"A5/TR5d8mUgjU+g4rk8Kb4Mu0UlXjIB0ttov0DiNewNwIRt18jA8+o+u3dpjq+sW\n"
"T8KOEUt+zwvo/7V3LvSye0rgTBIlDHCNAymg4VMk7BPZ7hm/ELNKjD+Jo2FR3qyH\n"
"B5T0Y3HsLuJvW5iB4YlcNHlsdu87kGJ55tukmi8mxdAQ4Q7e2RCOFvu396j3x+UC\n"
"B5iPNgiV5+I3lg02dZ77DnKxHZu8A/lJBdiB3QW0KtZB6awBdpUKD9jf1b0SHzUv\n"
"KBds0pjBqAlkd25HN7rOrFleaJ1/ctaJxQZBKT5ZPt0m9STJEadao0xAH0ahmbWn\n"
"OlFuhjuefXKnEgV4We0+UXgVCwOPjdAvBbI+e0ocS3MFEvzG6uBQE3xDk3SzynTn\n"
"jh8BCNAw1FtxNrQHusEwMFxIt4I7mKZ9YIqioymCzLq9gwQbooMDQaHWBfEbwrbw\n"
"qHyGO0aoSCqI3Haadr8faqU9GY/rOPNk3sgrDQoo//fb4hVC1CLQJ13hef4Y53CI\n"
"rU7m2Ys6xt0nUW7/vGT1M0NPAgMBAAGjQjBAMA4GA1UdDwEB/wQEAwIBBjAPBgNV\n"
"HRMBAf8EBTADAQH/MB0GA1UdDgQWBBR5tFnme7bl5AFzgAiIyBpY9umbbjANBgkq\n"
"hkiG9w0BAQsFAAOCAgEAVR9YqbyyqFDQDLHYGmkgJykIrGF1XIpu+ILlaS/V9lZL\n"
"ubhzEFnTIZd+50xx+7LSYK05qAvqFyFWhfFQDlnrzuBZ6brJFe+GnY+EgPbk6ZGQ\n"
"3BebYhtF8GaV0nxvwuo77x/Py9auJ/GpsMiu/X1+mvoiBOv/2X/qkSsisRcOj/KK\n"
"NFtY2PwByVS5uCbMiogziUwthDyC3+6WVwW6LLv3xLfHTjuCvjHIInNzktHCgKQ5\n"
"ORAzI4JMPJ+GslWYHb4phowim57iaztXOoJwTdwJx4nLCgdNbOhdjsnvzqvHu7Ur\n"
"TkXWStAmzOVyyghqpZXjFaH3pO3JLF+l+/+sKAIuvtd7u+Nxe5AW0wdeRlN8NwdC\n"
"jNPElpzVmbUq4JUagEiuTDkHzsxHpFKVK7q4+63SM1N95R1NbdWhscdCb+ZAJzVc\n"
"oyi3B43njTOQ5yOf+1CceWxG1bQVs5ZufpsMljq4Ui0/1lvh+wjChP4kqKOJ2qxq\n"
"4RgqsahDYVvTH9w7jXbyLeiNdd8XM2w9U/t7y0Ff/9yi0GE44Za4rF2LN9d11TPA\n"
"mRGunUHBcnWEvgJBQl9nJEiU0Zsnvgc/ubhPgXRR4Xq37Z0j4r7g1SgEEzwxA57d\n"
"emyPxgcYxn/eR44/KJ4EBs+lVDR3veyJm+kXQ99b21/+jh5Xos1AnX5iItreGCc=\n"
"-----END CERTIFICATE-----\n";

// =========================
// PORT PIN MAP
// =========================
#define PINBUTTON    1
#define PINBUTTON2   3
#define PINLED       4
#define PINSCTADC    0
#define PINDS1       5
#define PINDS2       6
#define PINI2CSDA    8
#define PINI2CSCL    9
#define PINBUZZER    10

OneWire oneWire1(PINDS1);
OneWire oneWire2(PINDS2);
DallasTemperature ds1(&oneWire1);
DallasTemperature ds2(&oneWire2);
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
bool maintenanceRequestPending = false;

// =========================
// OFFSET CALIBRATION STATE MACHINE (v4 protocol, DS18B20 adaptation)
// Backend approves via "startcalibration"; the supply probe (DS2) must drop
// CALIB_REQUIRED_DELTA °C below the captured baseline within 10 minutes.
// =========================
const unsigned long CALIB_TIMEOUT_MS = 10UL * 60UL * 1000UL;
const unsigned long CALIB_APPROVAL_TIMEOUT_MS = 10000UL;
const float CALIB_REQUIRED_DELTA = 8.0;

enum CalibState {
  CALIBIDLE = 0,
  CALIBWAITAPPROVAL,
  CALIBBASELINEWAIT,
  CALIBRUNNING
};

CalibState calibState = CALIBIDLE;
unsigned long calibApprovalRequestedAt = 0;
unsigned long calibStartedAt = 0;
unsigned long calibLastProgressAt = 0;
float calibBaseDS1 = 0.0, calibBaseDS2 = 0.0;
float calibFinalDS1 = 0.0, calibFinalDS2 = 0.0;
bool calibrationSavePending = false;

// =========================
// AVERAGING
// =========================
unsigned long lastSampleTime = 0;
const unsigned long SAMPLE_INTERVAL = 2000;
int sampleCount = 0;
const int MAX_SAMPLES = 5;

float sumDS1T = 0;
float sumDS2T = 0;
float sumBME280T = 0, sumBME280H = 0, sumBME280P = 0;
float sumCurrentA = 0.0;

// Per-metric valid sample counters (decoupled from sampleCount timing)
int validDS1T = 0;
int validDS2T = 0;
int validCurrentA = 0;

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
unsigned long lastWiFiRetry = 0;
unsigned long lastMqttRetry = 0;
unsigned long lastCheckinTime = 0;

// =========================
// LED STATE MACHINE
// =========================
unsigned long lastIdleBlink = 0;
float lastAvgCurrent = 0.0;  // For LED state machine

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

  String topic = "iot/production/nodes/" + deviceMac + "/events";
  Serial.print("TX EVENT -> ");
  Serial.println(topic);
  Serial.println(json);

  client.publish(topic.c_str(), json.c_str());
}

void publishCheckin() {
  if (!client.connected()) return;
  String ev = "{\"mac\":\"" + deviceMac + "\",\"event\":\"checkin\"}";
  publishEventJson(ev);
  lastCheckinTime = millis();
  Serial.println("TX CHECKIN");
}

bool publishTelemetry(const String& payload) {
  if (!client.connected()) {
    Serial.println("TX TELEMETRY skipped: MQTT not connected");
    Serial.println(payload);
    return false;
  }

  String topic = "iot/production/nodes/" + deviceMac + "/telemetry";
  Serial.print("TX TELEMETRY -> ");
  Serial.println(topic);
  Serial.println(payload);

  bool ok = client.publish(topic.c_str(), payload.c_str());

  if (!ok) {
    Serial.println("TX TELEMETRY FAILED: publish returned false");
  }
  return ok;
}

void publishCalibrationProgress(float ds2, float baseDs2, float delta) {
  String ev = "{\"mac\":\"" + deviceMac + "\",\"event\":\"calibration_progress\",\"ds2\":" +
              jnum(ds2, 2) + ",\"base_ds2\":" + jnum(baseDs2, 2) +
              ",\"delta\":" + jnum(delta, 2) + "}";
  publishEventJson(ev);
}

void publishCalibrationFailRequest(unsigned long elapsedMs, const String& reason) {
  String ev = "{\"mac\":\"" + deviceMac + "\",\"event\":\"calibration_fail_request\",\"elapsedms\":" +
              String(elapsedMs) + ",\"reason\":\"" + reason + "\"}";
  publishEventJson(ev);
}

unsigned long lastConfigRequestTime = 0;

void requestBackendConfig() {
  String ev = "{\"mac\":\"" + deviceMac + "\",\"event\":\"event_request_config\"}";
  publishEventJson(ev);
  lastConfigRequestTime = millis();
  Serial.println("Requested backend config.");
}

// =========================
// CALIBRATION STATE MACHINE
// =========================
void runCalibrationStateMachine(unsigned long now, float avgDS1T, float avgDS2T,
                                bool ds1Valid, bool ds2Valid) {
  if (calibState == CALIBIDLE) return;

  // Overall 10-minute timeout covers baseline wait + running phases.
  if (calibState != CALIBWAITAPPROVAL && now - calibStartedAt >= CALIB_TIMEOUT_MS) {
    Serial.println("CALIBRATION TIMEOUT (10 min).");
    beepLongFailTwice();
    publishCalibrationFailRequest(now - calibStartedAt, "timeout10min");
    calibState = CALIBIDLE;
    calibStartedAt = 0;
    printWorkflow();
    return;
  }

  if (calibState == CALIBWAITAPPROVAL) {
    if (now - calibApprovalRequestedAt >= CALIB_APPROVAL_TIMEOUT_MS) {
      Serial.println("CALIBRATION APPROVAL TIMEOUT.");
      beepLongFailTwice();
      publishCalibrationFailRequest(now - calibApprovalRequestedAt, "approval_timeout");
      calibState = CALIBIDLE;
      calibApprovalRequestedAt = 0;
      printWorkflow();
    }
    return;
  }

  if (calibState == CALIBBASELINEWAIT) {
    if (ds1Valid && ds2Valid) {
      calibBaseDS1 = avgDS1T;
      calibBaseDS2 = avgDS2T;
      calibLastProgressAt = now;
      calibState = CALIBRUNNING;
      Serial.printf("CALIBRATION BASELINE CAPTURED -> DS1=%.2f DS2=%.2f\n", calibBaseDS1, calibBaseDS2);
      publishCalibrationProgress(calibBaseDS2, calibBaseDS2, 0.0);
      printWorkflow();
    }
    return;
  }

  // CALIBRUNNING: monitor the supply probe (DS2) drop. The drop is SIGNED:
  // only cooling counts — a temperature rise must never complete calibration.
  if (!ds1Valid || !ds2Valid) {
    Serial.println("CALIBRATION: invalid sensor window, skipping.");
    return;
  }

  float drop = calibBaseDS2 - avgDS2T;
  if (now - calibLastProgressAt >= 2000) {
    calibLastProgressAt = now;
    publishCalibrationProgress(avgDS2T, calibBaseDS2, drop);
    Serial.printf("CALIBRATION PROGRESS -> DS2=%.2f base=%.2f drop=%.2f\n", avgDS2T, calibBaseDS2, drop);
  }

  if (drop >= CALIB_REQUIRED_DELTA) {
    calibFinalDS1 = avgDS1T;
    calibFinalDS2 = avgDS2T;
    unsigned long elapsed = now - calibStartedAt;
    calibState = CALIBIDLE;
    calibrationSavePending = true;

    String ev = "{\"mac\":\"" + deviceMac + "\",\"event\":\"calibration_success_request\",\"elapsedms\":" +
                String(elapsed) + ",\"deltaT\":" + jnum(drop, 2) +
                ",\"base\":{\"ds1\":" + jnum(calibBaseDS1, 2) + ",\"ds2\":" + jnum(calibBaseDS2, 2) +
                "},\"final\":{\"ds1\":" + jnum(calibFinalDS1, 2) + ",\"ds2\":" + jnum(calibFinalDS2, 2) + "}}";
    publishEventJson(ev);
    Serial.println("CALIBRATION DROP REACHED -> waiting backend save ack...");
    printWorkflow();
  }
}

void resetRuntimeFlowForPair() {
  calibrationAcked = false;
  maintenanceRequestPending = false;
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
    // Dryers do not require calibration — allow telemetry/maintenance immediately
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
    if (message == "restore:offsetcalibrationneeded") {
        calibrationAcked = false;
        calibrationSavePending = false;
        Serial.println("RESTORE -> need calibration");
        printWorkflow();
        return;
    }
    if (message == "restore:normal") {
        calibrationAcked = true;
        calibrationSavePending = false;
        Serial.println("RESTORE -> normal (maintenance allowed)");
        publishCheckin();  // Immediately tell backend we're alive
        printWorkflow();
        return;
    }

    // Backend approved the Button 2 calibration request -> capture baseline
    if (message == "startcalibration") {
        if (calibState == CALIBWAITAPPROVAL) {
            calibState = CALIBBASELINEWAIT;
            calibStartedAt = millis();
            Serial.println("CALIBRATION APPROVED -> capturing baseline (AC must be OFF)");
            printWorkflow();
        }
        return;
    }

    // Baseline configured (v2 manual input)
    if (message == "baseline:set") {
        Serial.println("BACKEND -> baseline configuration acknowledged");
        beepSuccess();
        printWorkflow();
        return;
    }

    // Offset calibration success / fail
    if (message == "offsetcalibrationsuccessack") {
        Serial.println("BACKEND SUCCESS -> offset calibration saved");
        beepSuccess();
        calibrationAcked = true;
        calibrationSavePending = false;
        calibState = CALIBIDLE;
        printWorkflow();
        return;
    }
    if (message == "offsetcalibrationfailack") {
        Serial.println("BACKEND FAIL -> offset calibration failed");
        beepLongFailTwice();
        calibrationSavePending = false;
        calibState = CALIBIDLE;
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
        Serial.println("BACKEND DENY -> device busy");
        beepLongFail(900);
        maintenanceRequestPending = false;
        calibrationSavePending = false;
        calibState = CALIBIDLE;
        printWorkflow();
        return;
    }
}

static unsigned long lastConnBuzzer = 0;

void checkConnection() {
  unsigned long now = millis();

  // --- LED STATE MACHINE ---
  // Priority: running > connection down > idle
  if (isRunningByCurrent(lastAvgCurrent)) {
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
  if (WiFi.status() != WL_CONNECTED || !client.connected()) {
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
        client.subscribe(("iot/production/nodes/" + deviceMac + "/control").c_str());
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

// =========================
// BUTTONS
// =========================
void handleButtons() {
  unsigned long now = millis();
  bool b1 = (digitalRead(PINBUTTON) == LOW);

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
      } else if (maintenanceRequestPending) {
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

  // ---------- BUTTON 2: OFFSET CALIBRATION (HVAC only, 5 s) ----------
  bool b2 = (digitalRead(PINBUTTON2) == LOW);
  if (b2) {
    if (btn2State == 0) {
      btn2PressedAt = now;
      btn2State = 1;
    } else if (btn2State == 1 && now - btn2PressedAt >= 5000) {
      btn2State = 2;
      Serial.println("BUTTON 2 -> 5s threshold reached (offset calibration)");

      if (!isPaired) {
        Serial.println("BUTTON 2 denied: node not paired yet.");
        beepLongFail(700);
      } else if (applianceType == "Dryer") {
        Serial.println("BUTTON 2 denied: Dryer does not require offset calibration.");
        beepLongFail(700);
      } else if (calibrationSavePending || maintenanceRequestPending || calibState != CALIBIDLE) {
        // silently ignore if a calibration request is already in progress
      } else {
        calibState = CALIBWAITAPPROVAL;
        calibApprovalRequestedAt = now;
        String ev = "{\"mac\":\"" + deviceMac + "\",\"event\":\"event_button2_offset_calibration_request\"}";
        Serial.println("BUTTON 2 -> offset calibration request sent (waiting backend approval)");
        beepRequest();
        publishEventJson(ev);
        printWorkflow();
      }
    }
  } else {
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
    float avgDS1T = validDS1T > 0 ? sumDS1T / validDS1T : NAN;
    float avgDS2T = validDS2T > 0 ? sumDS2T / validDS2T : NAN;

    payload += "\"DS1Temp\":" + jnum(avgDS1T, 1) + ",";
    payload += "\"DS2Temp\":" + jnum(avgDS2T, 1) + ",";
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

  sumDS1T = 0;
  sumDS2T = 0;
  sumBME280T = sumBME280H = sumBME280P = 0;
  sumCurrentA = 0.0;

  validDS1T = 0;
  validDS2T = 0;
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
    // Production: HVAC needs explicit calibration ack; Dryer is always ready
    calibrationAcked = (applianceType == "Dryer");
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

  ds1.begin();
  ds2.begin();
  ds1.setWaitForConversion(false);
  ds2.setWaitForConversion(false);

  Wire.begin(PINI2CSDA, PINI2CSCL);
  Wire.setClock(100000L);
  if (!bme.begin(0x76, &Wire)) {
    Serial.println("BME280 not found at 0x76");
  } else {
    Serial.println("BME280 OK");
    delay(100);
    bme.setSampling(Adafruit_BME280::MODE_NORMAL,
                    Adafruit_BME280::SAMPLING_X2,
                    Adafruit_BME280::SAMPLING_X16,
                    Adafruit_BME280::SAMPLING_X1,
                    Adafruit_BME280::FILTER_X16,
                    Adafruit_BME280::STANDBY_MS_62_5);
    delay(50);
  }

  // Dummy initial request/read on the two DS18B20 buses to prime the sensors
  ds1.requestTemperatures();
  ds2.requestTemperatures();
  delay(800);
  (void)ds1.getTempCByIndex(0);
  (void)ds2.getTempCByIndex(0);

  espClient.setCACert(ca_cert);
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

  unsigned long now = millis();

  if (now - lastSampleTime >= SAMPLE_INTERVAL) {

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
      // HVAC: two independent DS18B20 buses
      ds1.requestTemperatures();
      ds2.requestTemperatures();

      unsigned long dsWaitStart = millis();
      while (millis() - dsWaitStart < 800) {
        delay(1);
        esp_task_wdt_reset();
      }

      float t1 = ds1.getTempCByIndex(0);
      float t2 = ds2.getTempCByIndex(0);

      if (!isnan(t1) && t1 > -50.0 && t1 < 120.0) {
        sumDS1T += t1;
        validDS1T++;
      }
      if (!isnan(t2) && t2 > -50.0 && t2 < 120.0) {
        sumDS2T += t2;
        validDS2T++;
      }
    }

    float currentVal = readCurrentIrms();
    lastAvgCurrent = currentVal;  // Track instantaneous state for LED
    sumCurrentA += currentVal;
    validCurrentA++;

    sampleCount++;
  }

  if (sampleCount >= MAX_SAMPLES) {
    float avgDS1T = validDS1T > 0 ? sumDS1T / validDS1T : NAN;
    float avgDS2T = validDS2T > 0 ? sumDS2T / validDS2T : NAN;
    bool ds1Valid = validDS1T > 0;
    bool ds2Valid = validDS2T > 0;

    // Run the offset-calibration state machine on each completed sample window.
    runCalibrationStateMachine(millis(), avgDS1T, avgDS2T, ds1Valid, ds2Valid);

    // Send telemetry continuously once paired (running or idle). Calibration
    // state is reported via the "calstate" field; the backend shows the live
    // supply-temperature drop while calibration is running.
    if (calibrationAcked || isPaired) {
      String basePayload = buildTelemetryPayload();
      resetAverages();

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
