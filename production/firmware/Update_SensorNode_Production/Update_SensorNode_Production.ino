#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <HTTPUpdate.h>
#include <MQTTClient.h>
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
#include <mbedtls/sha256.h>

// WiFi + MQTT credentials live in secrets.h (gitignored).
// Copy secrets.h.example to secrets.h and fill in your own values.
#include "secrets.h"

// DigiCert Global Root G2 CA Certificate (DigiCert / EMQX Cloud)
// Required for WiFiClientSecure to validate the broker TLS certificate
// Source: https://www.digicert.com/kb/digicert-root-certificates.htm
const char* ca_cert =
"-----BEGIN CERTIFICATE-----\n"
"MIIDjjCCAnagAwIBAgIQAzrx5qcRqaC7KGSxHQn65TANBgkqhkiG9w0BAQsFADBh\n"
"MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3\n"
"d3cuZGlnaWNlcnQuY29tMSAwHgYDVQQDExdEaWdpQ2VydCBHbG9iYWwgUm9vdCBH\n"
"MjAeFw0xMzA4MDExMjAwMDBaFw0zODAxMTUxMjAwMDBaMGExCzAJBgNVBAYTAlVT\n"
"MRUwEwYDVQQKEwxEaWdpQ2VydCBJbmMxGTAXBgNVBAsTEHd3dy5kaWdpY2VydC5j\n"
"b20xIDAeBgNVBAMTF0RpZ2lDZXJ0IEdsb2JhbCBSb290IEcyMIIBIjANBgkqhkiG\n"
"9w0BAQEFAAOCAQ8AMIIBCgKCAQEAuzfNNNx7a8myaJCtSnX/RrohCgiN9RlUyfuI\n"
"2/Ou8jqJkTx65qsGGmvPrC3oXgkkRLpimn7Wo6h+4FR1IAWsULecYxpsMNzaHxmx\n"
"1x7e/dfgy5SDN67sH0NO3Xss0r0upS/kqbitOtSZpLYl6ZtrAGCSYP9PIUkY92eQ\n"
"q2EGnI/yuum06ZIya7XzV+hdG82MHauVBJVJ8zUtluNJbd134/tJS7SsVQepj5Wz\n"
"tCO7TG1F8PapspUwtP1MVYwnSlcUfIKdzXOS0xZKBgyMUNGPHgm+F6HmIcr9g+UQ\n"
"vIOlCsRnKPZzFBQ9RnbDhxSJITRNrw9FDKZJobq7nMWxM4MphQIDAQABo0IwQDAP\n"
"BgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIBhjAdBgNVHQ4EFgQUTiJUIBiV\n"
"5uNu5g/6+rkS7QYXjzkwDQYJKoZIhvcNAQELBQADggEBAGBnKJRvDkhj6zHd6mcY\n"
"1Yl9PMWLSn/pvtsrF9+wX3N3KjITOYFnQoQj8kVnNeyIv/iPsGEMNKSuIEyExtv4\n"
"NeF22d+mQrvHRAiGfzZ0JFrabA0UWTW98kndth/Jsw1HKj2ZL7tcu7XUIOGZX1NG\n"
"Fdtom/DzMNU+MeKNhJ7jitralj41E6Vf8PlwUHBHQRFXGU7Aj64GxJUTFy8bJZ91\n"
"8rGOmaFvE7FBcf6IKshPECBV1/MUReXgRPTqh5Uykw7+U0b6LJ3/iyK5S9kJRaTe\n"
"pLiaWN0bfVKfjllDiIGknibVb63dDcY3fe0Dkhvld1927jyNxF1WW6LZZm6zNTfl\n"
"MrY=\n"
"-----END CERTIFICATE-----\n";

// =========================
// BUILD VARIANT + OTA CONFIG
// =========================
// FIRST_BOOT_TRIPLE_BEEP = 0 -> variant A: normal behavior + OTA support.
// FIRST_BOOT_TRIPLE_BEEP = 1 -> variant B: A, plus three beeps on the FIRST
//   boot after flashing (once ever; guarded by the NVS flag "fb3b").
// Compile twice (flip the flag) and export one binary per variant.
#define FIRST_BOOT_TRIPLE_BEEP 0

// Release version: bump per release; MUST match the OTA .bin file name
// (node_<FIRMWARE_VERSION>.bin). Incoming OTA triggers for the version the
// node already runs are ignored (idempotent re-trigger safety).
#define FIRMWARE_VERSION "20261016-1"

// ISRG Root X1 CA Certificate (Let's Encrypt)
// Used ONLY for the OTA HTTP(S) download client. The MQTT client keeps using
// the DigiCert root (EMQX Cloud). Source:
// https://letsencrypt.org/certs/isrgrootx1.pem
const char* ota_ca_cert =
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
// 1024-byte read+write buffers: control RX strings are tiny, telemetry TX
// JSON fits well within 1024 (replaces PubSubClient's setBufferSize(1024)).
// QoS 1 (below) needs a real MQTT stack — PubSubClient only does QoS 0.
MQTTClient client(1024);
String deviceMac = "";

Preferences prefs;

// =========================
// NVS NETWORK CONFIG (netcfg)
// =========================
// Per-appliance WiFi/MQTT credentials pushed by the backend
// (setwifi:/setmqtt: on the control topic) and persisted here. They SURVIVE
// OTA updates, which makes binaries network-portable: any build flashed to
// any node connects with ITS OWN credentials. Empty = fall back to the
// compiled-in secrets.h values (backward compatible with existing nodes).
String netSsid = "";
String netPass = "";
String netMqttUser = "";
String netMqttPass = "";

void loadNetConfig() {
  prefs.begin("netcfg", true);
  netSsid = prefs.getString("ssid", "");
  netPass = prefs.getString("pass", "");
  netMqttUser = prefs.getString("muser", "");
  netMqttPass = prefs.getString("mpass", "");
  prefs.end();
  if (netSsid.length() > 0) {
    Serial.print("NETCFG: WiFi creds loaded from NVS (ssid=");
    Serial.print(netSsid);
    Serial.println(")");
  }
  if (netMqttUser.length() > 0) {
    Serial.println("NETCFG: MQTT creds loaded from NVS");
  }
}

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

  // QoS 1 so the broker acks and can queue for a durable subscription;
  // never retained.
  client.publish(topic.c_str(), json.c_str(), false, 1);
}

void publishCheckin() {
  if (!client.connected()) return;
  String ev = "{\"mac\":\"" + deviceMac + "\",\"event\":\"checkin\",\"fw\":\"" FIRMWARE_VERSION "\"}";
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

  // QoS 1: publish() returning true now means the broker accepted AND acked.
  bool ok = client.publish(topic.c_str(), payload.c_str(), false, 1);

  if (!ok) {
    Serial.println("TX TELEMETRY FAILED: publish returned false");
  }
  return ok;
}

// The reference probe is DS1 (Port 1): the calibration assumes both probes
// sit at the SAME point and treats DS1 as correct, so the drop that proves
// real cooling is measured on DS1, and DS2 is only checked to follow it.
void publishCalibrationProgress(float ds1, float baseDs1, float delta) {
  String ev = "{\"mac\":\"" + deviceMac + "\",\"event\":\"calibration_progress\",\"ds1\":" +
              jnum(ds1, 2) + ",\"base_ds1\":" + jnum(baseDs1, 2) +
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
  String ev = "{\"mac\":\"" + deviceMac + "\",\"event\":\"event_request_config\",\"fw\":\"" FIRMWARE_VERSION "\"}";
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
      // DS1 is the reference probe: the visual tracks its value and drop.
      publishCalibrationProgress(calibBaseDS1, calibBaseDS1, 0.0);
      printWorkflow();
    }
    return;
  }

  // CALIBRUNNING: monitor the REFERENCE probe (DS1) drop. The drop is SIGNED:
  // only cooling counts — a temperature rise must never complete calibration.
  // DS2 only has to follow (backend gate >= 2.5 C), proving both probes sit at
  // the same point and S2 is healthy.
  if (!ds1Valid || !ds2Valid) {
    Serial.println("CALIBRATION: invalid sensor window, skipping.");
    return;
  }

  float drop = calibBaseDS1 - avgDS1T;
  if (now - calibLastProgressAt >= 2000) {
    calibLastProgressAt = now;
    publishCalibrationProgress(avgDS1T, calibBaseDS1, drop);
    Serial.printf("CALIBRATION PROGRESS -> DS1=%.2f base=%.2f drop=%.2f (DS2=%.2f)\n", avgDS1T, calibBaseDS1, drop, avgDS2T);
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
  WiFi.begin(netSsid.length() > 0 ? netSsid.c_str() : WIFI_SSID,
             netPass.length() > 0 ? netPass.c_str() : WIFI_PASS);

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

// =========================
// POST-OTA SELF-TEST GUARD
// =========================
// Set right before an OTA reboot (NVS "otapend"). On boot it arms a 10-min
// timer: the first successful MQTT connect clears it (new firmware works);
// on timeout the node rolls back to the previous app slot (bad build, TLS
// mismatch, wrong host) — no USB visit needed. Harmless when the BROKER is
// down: the old firmware could not connect either, and the node keeps
// retrying whichever image it ends up on.
bool otaPending = false;
unsigned long otaBootAt = 0;
const unsigned long OTA_SELFTEST_MS = 10UL * 60UL * 1000UL;

void clearOtaPending() {
  if (!otaPending) return;
  otaPending = false;
  prefs.begin("nodecfg", false);
  prefs.putBool("otapend", false);
  prefs.end();
  Serial.println("OTA self-test PASSED (MQTT connected on new firmware)");
  publishEventJson("{\"mac\":\"" + deviceMac + "\",\"event\":\"ota_confirmed\"}");
}

void checkOtaSelfTest(unsigned long now) {
  if (!otaPending || now - otaBootAt < OTA_SELFTEST_MS) return;
  otaPending = false;
  prefs.begin("nodecfg", false);
  prefs.putBool("otapend", false);
  prefs.end();
  Serial.println("OTA self-test FAILED (no MQTT in 10 min) -> rolling back");
  publishEventJson("{\"mac\":\"" + deviceMac + "\",\"event\":\"ota_rollback\"}");
  delay(500);  // let the event flush
  if (Update.rollBack()) {
    ESP.restart();
  }
  Serial.println("OTA rollback refused; keeping current firmware");
}

// =========================
// OTA FIRMWARE UPDATE (see OTA.md)
// =========================
// Command (control topic): ota:<url>:<sha256-hex>. The URL host must match
// OTA_ALLOWED_HOST (secrets.h). The download goes straight to our server
// over HTTPS — it never transits MQTT (free-broker friendly). Update flow:
//   WAITING (gates: calibration soft-wait, offline-buffer drain) -> DOWNLOADING
//   -> verify SHA-256 -> switch slot -> reboot. Any failure keeps the current
//   firmware running and reports ota_fail on the events topic.
enum OtaPhase { OTA_IDLE, OTA_WAITING, OTA_DOWNLOADING };
OtaPhase otaPhase = OTA_IDLE;
String otaUrl = "";
String otaSha256 = "";
String otaFileName = "";
unsigned long otaRequestedAt = 0;
unsigned long otaWaitingNotifiedAt = 0;
const unsigned long OTA_BUFFER_DRAIN_TIMEOUT_MS = 5UL * 60UL * 1000UL;

void publishOtaEvent(const char* event, const String& extra = "") {
  String ev = "{\"mac\":\"" + deviceMac + "\",\"event\":\"" + event + "\"";
  if (otaFileName.length() > 0) ev += ",\"file\":\"" + otaFileName + "\"";
  if (extra.length() > 0) ev += "," + extra;
  ev += "}";
  publishEventJson(ev);
}

void otaFail(const String& code) {
  Serial.println("OTA FAILED: " + code);
  publishOtaEvent("ota_fail", "\"code\":\"" + code + "\"");
  otaPhase = OTA_IDLE;
  otaUrl = "";
  otaSha256 = "";
  otaFileName = "";
}

void notifyOtaWaiting(unsigned long now, const char* reason) {
  if (now - otaWaitingNotifiedAt >= 15000) {
    otaWaitingNotifiedAt = now;
    publishOtaEvent("ota_waiting", "\"reason\":\"" + String(reason) + "\"");
    Serial.printf("OTA: waiting (%s), buffer=%d\n", reason, (int)offlineQueue.size());
  }
}

// Stage an OTA request from the control topic (never flash inside the MQTT
// callback — the actual work happens in handleOta() on the main loop).
void handleOtaCommand(const String& arg) {
  // format: <url>:<sha256-hex> (split at the LAST colon; the URL itself
  // contains colons for the scheme/port)
  int idx = arg.lastIndexOf(':');
  if (idx <= 0 || idx == (int)arg.length() - 1) {
    Serial.println("OTA: malformed command");
    return;
  }
  String url = arg.substring(0, idx);
  String sha = arg.substring(idx + 1);
  sha.toLowerCase();
  if (sha.length() != 64) {
    Serial.println("OTA: bad sha256 digest");
    return;
  }
  bool hostOk = url.startsWith("https://" OTA_ALLOWED_HOST "/") ||
                url.startsWith("http://" OTA_ALLOWED_HOST "/");
  if (!hostOk) {
    Serial.println("OTA: host not in allowlist");
    return;
  }
  if (otaPhase != OTA_IDLE) {
    Serial.println("OTA: update already in progress");
    return;
  }
  otaUrl = url;
  otaSha256 = sha;
  int slash = url.lastIndexOf('/');
  otaFileName = (slash >= 0) ? url.substring(slash + 1) : url;

  // Idempotent triggers: a .bin for the version we already run is a no-op.
  if (otaFileName.startsWith("node_") && otaFileName.endsWith(".bin")) {
    String offered = otaFileName.substring(5, otaFileName.length() - 4);
    if (offered == FIRMWARE_VERSION) {
      Serial.println("OTA: already running " + offered + " -> no-op");
      publishOtaEvent("ota_done", "\"skipped\":true");
      otaUrl = "";
      otaSha256 = "";
      otaFileName = "";
      return;
    }
  }

  otaRequestedAt = millis();
  otaWaitingNotifiedAt = 0;
  otaPhase = OTA_WAITING;
  publishOtaEvent("ota_received");
  Serial.println("OTA: staged " + otaFileName);
}

// setwifi:<ssid>:<pass> — persist per-appliance WiFi creds and reconnect.
void handleSetWifi(const String& arg) {
  int idx = arg.indexOf(':');
  if (idx <= 0) {
    Serial.println("SETWIFI: malformed");
    return;
  }
  String ssid = arg.substring(0, idx);
  String pass = arg.substring(idx + 1);
  prefs.begin("netcfg", false);
  prefs.putString("ssid", ssid);
  prefs.putString("pass", pass);
  prefs.end();
  netSsid = ssid;
  netPass = pass;
  Serial.println("SETWIFI: saved, reconnecting...");
  WiFi.disconnect();
  delay(200);
  WiFi.begin(netSsid.c_str(), netPass.c_str());
}

// setmqtt:<user>:<pass> — persist per-appliance MQTT creds and reconnect.
void handleSetMqtt(const String& arg) {
  int idx = arg.indexOf(':');
  if (idx <= 0) {
    Serial.println("SETMQTT: malformed");
    return;
  }
  String user = arg.substring(0, idx);
  String pass = arg.substring(idx + 1);
  prefs.begin("netcfg", false);
  prefs.putString("muser", user);
  prefs.putString("mpass", pass);
  prefs.end();
  netMqttUser = user;
  netMqttPass = pass;
  Serial.println("SETMQTT: saved, reconnecting MQTT...");
  client.disconnect();
}

// Stream the download into the INACTIVE OTA slot while computing SHA-256.
void startOtaDownload() {
  Serial.println("OTA: downloading " + otaUrl);
  WiFiClientSecure otaClient;
  otaClient.setCACert(ota_ca_cert);
  otaClient.setTimeout(60);

  HTTPClient http;
  http.setTimeout(60000);
  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
  if (!http.begin(otaClient, otaUrl)) {
    otaFail("http_begin");
    return;
  }
  int httpCode = http.GET();
  if (httpCode != 200) {
    http.end();
    otaFail("http_" + String(httpCode));
    return;
  }
  int total = http.getSize();
  if (total <= 0) {
    http.end();
    otaFail("no_size");
    return;
  }
  if (!Update.begin(total, U_FLASH)) {
    http.end();
    otaFail("update_begin");
    return;
  }

  mbedtls_sha256_context ctx;
  mbedtls_sha256_init(&ctx);
  mbedtls_sha256_starts(&ctx, 0);

  WiFiClient* stream = http.getStreamPtr();
  uint8_t buf[2048];
  int written = 0;
  int lastPct = -1;
  while (written < total) {
    esp_task_wdt_reset();
    client.loop();  // keep the MQTT session alive during the download
    int avail = stream->available();
    if (avail <= 0) {
      if (!stream->connected()) break;
      delay(10);
      continue;
    }
    int n = stream->read(buf, (avail > (int)sizeof(buf)) ? sizeof(buf) : avail);
    if (n <= 0) continue;
    if (Update.write(buf, n) != n) {
      mbedtls_sha256_free(&ctx);
      http.end();
      otaFail("write");
      return;
    }
    mbedtls_sha256_update(&ctx, buf, n);
    written += n;
    int pct = (written * 100) / total;
    if (pct >= lastPct + 25) {
      lastPct = pct;
      publishOtaEvent("ota_progress", "\"pct\":" + String(pct));
    }
  }
  http.end();

  uint8_t digest[32];
  mbedtls_sha256_finish(&ctx, digest);
  mbedtls_sha256_free(&ctx);

  char hex[65];
  for (int i = 0; i < 32; i++) sprintf(hex + i * 2, "%02x", digest[i]);
  hex[64] = 0;
  if (String(hex) != otaSha256) {
    Update.abort();
    Serial.printf("OTA: digest mismatch got=%s want=%s\n", hex, otaSha256.c_str());
    otaFail("digest");
    return;
  }
  if (written != total) {
    Update.abort();
    otaFail("incomplete");
    return;
  }
  if (!Update.end(false)) {
    otaFail("update_end");
    return;
  }

  Serial.println("OTA: image verified, rebooting into new firmware");
  publishOtaEvent("ota_done");
  // Arm the post-OTA self-test guard BEFORE the reboot: if the new image
  // cannot reach the broker within 10 min it rolls itself back.
  prefs.begin("nodecfg", false);
  prefs.putBool("otapend", true);
  prefs.end();
  delay(500);  // let the event flush
  ESP.restart();
}

// Called every loop(): runs the staged update through its safety gates.
void handleOta() {
  if (otaPhase == OTA_IDLE) return;
  unsigned long now = millis();

  // Gate 1 (soft): let an active calibration session finish — interrupting it
  // would waste the user's probe work. It is time-boxed at 10 min by design
  // and self-recovers if rebooted mid-session.
  if (calibState != CALIBIDLE || calibrationSavePending) {
    notifyOtaWaiting(now, "calibration");
    return;
  }

  // Gate 2 (hard, data safety): the offline buffer lives in RAM and a reboot
  // wipes it. The node is connected (it received the command), so flushing
  // is fast; if the backlog will not drain in 5 min, abort CLEANLY — no
  // flash, no data lost.
  if (!offlineQueue.empty()) {
    notifyOtaWaiting(now, "buffer");
    if (now - otaRequestedAt > OTA_BUFFER_DRAIN_TIMEOUT_MS) {
      otaFail("buf_timeout");
    }
    return;
  }

  if (otaPhase == OTA_WAITING) {
    otaPhase = OTA_DOWNLOADING;
    publishOtaEvent("ota_started");
    startOtaDownload();
  }
}

void callback(String &topic, String &payload) {
    String message = payload;

    // Add this to remove hidden newlines or spaces!
    message.trim();

    Serial.print("RX CONTROL <- ");
    Serial.println(topic);
    Serial.println(message);

    // OTA firmware update (staged by handleOta() on the main loop — never
    // flash inside the MQTT callback)
    if (message.startsWith("ota:")) {
        handleOtaCommand(message.substring(4));
        return;
    }
    // Per-appliance network credentials (persisted in NVS netcfg)
    if (message.startsWith("setwifi:")) {
        handleSetWifi(message.substring(8));
        return;
    }
    if (message.startsWith("setmqtt:")) {
        handleSetMqtt(message.substring(8));
        return;
    }

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
  // Priority: calibration > running > connection down > idle
  if (calibState == CALIBBASELINEWAIT || calibState == CALIBRUNNING) {
    // Calibration: slow blink (1000ms)
    if (now - lastLedBlink >= 1000) {
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
  } else {
    // Idle: brief blink every 10s
    if (now - lastIdleBlink >= 10000) {
      lastIdleBlink = now;
      digitalWrite(PINLED, HIGH);
      delay(50);
      digitalWrite(PINLED, LOW);
    }
  }

  // --- CONNECTION DOWN BUZZER (every 10s; muted while calibrating) ---
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
      WiFi.begin(netSsid.length() > 0 ? netSsid.c_str() : WIFI_SSID,
             netPass.length() > 0 ? netPass.c_str() : WIFI_PASS);
    }
    return;
  }

  // MQTT down
  if (!client.connected()) {
    if (now - lastMqttRetry >= 5000) {
      lastMqttRetry = now;

      String clientId = "ESP32-" + deviceMac;
      Serial.print("WiFi OK. Connecting MQTT... ");

      if (client.connect(clientId.c_str(),
                         netMqttUser.length() > 0 ? netMqttUser.c_str() : mqtt_user,
                         netMqttPass.length() > 0 ? netMqttPass.c_str() : mqtt_pass)) {
        Serial.println("connected");
        clearOtaPending();
        client.subscribe(("iot/production/nodes/" + deviceMac + "/control").c_str(), 1);
        digitalWrite(PINLED, HIGH);
        requestBackendConfig();
      } else {
        // lwmqtt error code (lwmqtt_err_t) — arduino-mqtt has no state().
        Serial.print("failed err=");
        Serial.println((int)client.lastError());
      }
    }
    return;
  }

  // Retry config request: unpaired nodes re-ask every 10 s; paired nodes that
  // are not calibration-acked re-ask every 60 s — the boot-time request may
  // have been lost while the backend was down, and the firmware only persists
  // the calibration ack for Dryers (2026-10-14 incident). The backend answers
  // restore:* from DB truth, so re-asking is idempotent.
  unsigned long cfgInterval = isPaired ? 60000 : 10000;
  if ((!isPaired || !calibrationAcked) && now - lastConfigRequestTime >= cfgInterval) {
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
  Serial.println("Booting Sensor Node (OTA build " FIRMWARE_VERSION ")...");

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

  // Per-appliance network credentials (NVS netcfg) — loaded BEFORE WiFi so
  // the first connection already uses them when provisioned.
  loadNetConfig();

  // Post-OTA self-test: if the last boot was an OTA update, arm the guard —
  // cleared by the first successful MQTT connect, otherwise rollback.
  prefs.begin("nodecfg", true);
  otaPending = prefs.getBool("otapend", false);
  prefs.end();
  if (otaPending) {
    otaBootAt = millis();
    Serial.println("OTA self-test armed: 10 min to reach the broker");
  }

  pinMode(PINBUTTON, INPUT_PULLUP);
  pinMode(PINBUTTON2, INPUT_PULLUP);
  pinMode(PINLED, OUTPUT);
  pinMode(PINBUZZER, OUTPUT);

  buzzerOff();
  digitalWrite(PINLED, LOW);

#if FIRST_BOOT_TRIPLE_BEEP
  // Variant B only: three beeps on the FIRST boot after flashing, once ever
  // (NVS-guarded). Survives A->B->A: a later B reinstall stays silent.
  prefs.begin("nodecfg", false);
  if (!prefs.getBool("fb3b", false)) {
    prefs.putBool("fb3b", true);
    prefs.end();
    Serial.println("FIRST BOOT (variant B) -> 3 beeps");
    delay(400);
    beepShort(3, 150, 150);
  } else {
    prefs.end();
  }
#endif

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

  client.begin(mqtt_server, mqtt_port, espClient);
  client.onMessage(callback);
  client.setKeepAlive(15);

  Serial.println("System Ready.");
}

void loop() {
  esp_task_wdt_reset();
  checkConnection();
  handleOta();
  checkOtaSelfTest(millis());

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

    // Send telemetry when calibrated (running or idle) and ALSO while a
    // calibration session is active, so the dashboard can show BOTH probes
    // live (first and re-calibration). v4 behavior for a paired-but-
    // uncalibrated idle node is unchanged: nothing streams yet.
    bool calibrationActive = (calibState == CALIBBASELINEWAIT || calibState == CALIBRUNNING);
    if (calibrationAcked || calibrationActive) {
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
