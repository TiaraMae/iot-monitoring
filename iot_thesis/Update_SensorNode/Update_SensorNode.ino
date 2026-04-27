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

// =========================
// WIFI CONFIG
// =========================
const char* WIFI_SSID = "S23";
const char* WIFI_PASS = "goodjob01";

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

// =========================
// DEVICE TYPE / FLOW STATE
// =========================
String applianceType = "unpaired";   // unpaired, HVAC, Dryer
bool isPaired = false;

bool calibrationAcked = false;
bool calibrationSavePending = false;

bool baselineRequestPending = false;
bool baselineInProgress = false;
bool baselineAcked = false;

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
float sumCurrentA = 0;

// =========================
// CONNECTION STATUS
// =========================
unsigned long lastLedBlink = 0;
bool ledState = LOW;
unsigned long lastWiFiRetry = 0;
unsigned long lastMqttRetry = 0;

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

void beepLongFail(unsigned long ms = 1200) {
  buzzerOn();
  delay(ms);
  buzzerOff();
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
  if (baselineRequestPending) return "baseline-requested";
  if (baselineInProgress) return "baselining";
  if (!baselineAcked) return "need-baseline";
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

  digitalWrite(PINLED, LOW);
  client.publish(topic.c_str(), json.c_str());
  delay(30);
  digitalWrite(PINLED, HIGH);
}

void publishTelemetry(const String& payload) {
  if (!client.connected()) {
    Serial.println("TX TELEMETRY skipped: MQTT not connected");
    Serial.println(payload);
    return;
  }

  String topic = "iot/nodes/" + deviceMac + "/telemetry";
  Serial.print("TX TELEMETRY -> ");
  Serial.println(topic);
  Serial.println(payload);

  digitalWrite(PINLED, LOW);
  client.publish(topic.c_str(), payload.c_str());
  delay(30);
  digitalWrite(PINLED, HIGH);
}

void requestBackendConfig() {
  String ev = "{\"mac\":\"" + deviceMac + "\",\"event\":\"event_request_config\"}";
  publishEventJson(ev);
  Serial.println("Requested backend config.");
}

void resetRuntimeFlowForPair() {
  calibrationAcked = false;
  calibrationSavePending = false;
  baselineRequestPending = false;
  baselineInProgress = false;
  baselineAcked = false;
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
    beepShort(1, 100, 0); // 1 Beep for pairing success
    Serial.print("PAIR OK -> ");
    Serial.println(applianceType);
  } else {
    Serial.print("PAIR CONFIRMED AGAIN -> ");
    Serial.println(applianceType);
  }

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

    // --- STATE RESTORE HANDLERS ---
    if (message == "restore:calibrationneeded") {
        calibrationAcked = false;
        baselineAcked = false;
        baselineRequestPending = false;
        baselineInProgress = false;
        calibState = CALIBIDLE;
        Serial.println("RESTORE -> need calibration");
        printWorkflow();
        return;
    }
    if (message == "restore:baselinepending") {
        calibrationAcked = true;
        baselineAcked = false;
        baselineRequestPending = false;
        baselineInProgress = false;
        calibState = CALIBIDLE;
        Serial.println("RESTORE -> need baseline");
        printWorkflow();
        return;
    }
    if (message == "restore:normal") {
        calibrationAcked = true;
        baselineAcked = true;
        baselineRequestPending = false;
        baselineInProgress = false;
        calibState = CALIBIDLE;
        Serial.println("RESTORE -> normal (maintenance allowed)");
        printWorkflow();
        return;
    }

    // Calibration
    if (message == "startcalibration") {
        Serial.println("BACKEND CONFIRMED -> calibration start");
        beepConfirmed();
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
        beepLongFail(1200);
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
        baselineRequestPending = false;
        baselineInProgress = false;
        baselineAcked = false;
        calibState = CALIBIDLE;
        printWorkflow();
        return;
    }

    // Baseline
    if (message == "baselinestartack") {
        Serial.println("BACKEND CONFIRMED -> baseline started");
        beepConfirmed();
        calibrationAcked = true;
        calibState = CALIBIDLE;
        baselineRequestPending = false;
        baselineInProgress = true;
        printWorkflow();
        return;
    }
    if (message == "baselinesuccessack") {
        Serial.println("BACKEND SUCCESS -> baseline saved");
        beepSuccess();
        baselineRequestPending = false;
        baselineInProgress = false;
        baselineAcked = true;
        printWorkflow();
        return;
    }
    if (message == "baselinefailack") {
        Serial.println("BACKEND CANCEL/FAIL -> baseline rejected or cancelled");
        beepLongFail(1200);
        baselineRequestPending = false;
        baselineInProgress = false;
        printWorkflow();
        return;
    }

    // Maintenance
    if (message == "maintenanceack") {
        Serial.println("BACKEND SUCCESS -> maintenance logged");
        beepSuccess();
        maintenanceRequestPending = false;
        printWorkflow();
        return;
    }

    // Busy / generic deny
    if (message == "actiondenied:busy") {
        if (calibState == CALIBBASELINEWAIT || calibState == CALIBRUNNING || baselineInProgress || calibrationSavePending) {
            Serial.println("BACKEND DENY -> Ignored (Device is already successfully busy)");
            return;
        }
        Serial.println("BACKEND DENY -> device busy");
        beepLongFail(900);
        baselineRequestPending = false;
        maintenanceRequestPending = false;
        printWorkflow();
        return;
    }
}

void checkConnection() {
  unsigned long now = millis();

  // WiFi down
  if (WiFi.status() != WL_CONNECTED) {
    if (now - lastLedBlink >= 200) {
      lastLedBlink = now;
      ledState = !ledState;
      digitalWrite(PINLED, ledState);
    }

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
    if (now - lastLedBlink >= 500) {
      lastLedBlink = now;
      ledState = !ledState;
      digitalWrite(PINLED, ledState);
    }

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

  // All good
  digitalWrite(PINLED, HIGH);
}

// =========================
// SENSOR READS
// =========================
double readCurrentIrms() {
  unsigned long startMillis = millis();
  long sum = 0;
  double sumSquared = 0;
  int count = 0;

  while (millis() - startMillis < 200) {
    long raw = analogRead(PINSCTADC);
    sum += raw;
    sumSquared += (double)raw * (double)raw;
    count++;
    delay(1);
  }

  if (count == 0) return 0.0;

  float mean = (float)sum / count;
  float meanSquare = (float)(sumSquared / count);
  float variance = meanSquare - (mean * mean);
  if (variance < 0) variance = 0;

  float rmsADC = sqrt(variance);
  uint32_t trueVoltageMv = esp_adc_cal_raw_to_voltage((uint32_t)rmsADC, &adc1_chars);
  float cf = (applianceType == "Dryer") ? 37.0 : 11.0;
  return (trueVoltageMv / 1000.0) * cf;
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
    return;
  }

  calibDeltaT = fabs(p.t3 - calibBaseT3);

  Serial.print("Calibration running. T3=");
  Serial.print(p.t3, 2);
  Serial.print(" delta=");
  Serial.print(calibDeltaT, 2);
  Serial.print(" need=");
  Serial.println(CALIB_REQUIRED_DELTA_T, 2);

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
  if (calibState == CALIBBASELINEWAIT || calibState == CALIBRUNNING || 
      baselineInProgress || calibrationSavePending) {
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
      } else if (!calibrationAcked || !baselineAcked) {
        Serial.println("BUTTON 1 denied: finish calibration and baseline first.");
        beepLongFail(700);
      } else if (baselineRequestPending || maintenanceRequestPending || calibState != CALIBIDLE) {
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

  // ---------- BUTTON 2: CALIBRATION / BASELINE ----------
  if (b2) {
    if (btn2State == 0) {
      btn2PressedAt = now;
      btn2State = 1;
    } else if (btn2State == 1 && now - btn2PressedAt >= 2000) {
      btn2State = 2;

      if (!isPaired) {
        Serial.println("BUTTON 2 denied: node not paired yet.");
        beepLongFail(700);
      } else if (calibrationSavePending || baselineRequestPending || maintenanceRequestPending || calibState == CALIBWAITAPPROVAL) {
         // SILENTLY ignore
      } else {
        String ev = "{\"mac\":\"" + deviceMac + "\",\"event\":\"event_button2_action_request\"}";
        Serial.println("BUTTON 2 -> backend action request sent");
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
    payload += "\"BME280Temp\":" + jnum(sumBME280T / MAX_SAMPLES, 1) + ",";
    payload += "\"BME280Hum\":" + jnum(sumBME280H / MAX_SAMPLES, 1) + ",";
    payload += "\"BME280Pres\":" + jnum(sumBME280P / MAX_SAMPLES, 1) + ",";
  } else {
    payload += "\"DHT1Temp\":" + jnum(sumDHT1T / MAX_SAMPLES, 1) + ",";
    payload += "\"DHT1Hum\":" + jnum(sumDHT1H / MAX_SAMPLES, 1) + ",";
    payload += "\"DHT2Temp\":" + jnum(sumDHT2T / MAX_SAMPLES, 1) + ",";
    payload += "\"DHT2Hum\":" + jnum(sumDHT2H / MAX_SAMPLES, 1) + ",";
    payload += "\"DS18B20Temp\":" + jnum(sumDS18B20T / MAX_SAMPLES, 1) + ",";
  }

  payload += "\"CurrentA\":" + jnum(sumCurrentA / MAX_SAMPLES, 3);
  payload += "}";

  return payload;
}

void resetAverages() {
  sampleCount = 0;
  sumDHT1T = sumDHT1H = 0;
  sumDHT2T = sumDHT2H = 0;
  sumDS18B20T = 0;
  sumBME280T = sumBME280H = sumBME280P = 0;
  sumCurrentA = 0;
}

// =========================
// SETUP / LOOP
// =========================
void setup() {
  Serial.begin(115200);
  delay(3000);
  Serial.println("Booting Sensor Node...");

  pinMode(PINBUTTON, INPUT_PULLUP);
  pinMode(PINBUTTON2, INPUT_PULLUP);
  pinMode(PINLED, OUTPUT);
  pinMode(PINBUZZER, OUTPUT);

  buzzerOff();
  digitalWrite(PINLED, LOW);

  analogReadResolution(12);
  analogSetPinAttenuation(PINSCTADC, ADC_11db);
  esp_adc_cal_characterize(ADC_UNIT_1, ADC_ATTEN_DB_11, ADC_WIDTH_BIT_12, 1100, &adc1_chars);

  dht1.begin();
  dht2.begin();
  dsCoil.begin();

  Wire.begin(PINI2CSDA, PINI2CSCL);
  if (!bme.begin(0x76, &Wire)) {
    Serial.println("BME280 not found at 0x76.");
  } else {
    Serial.println("BME280 OK.");
  }

  dsCoil.requestTemperatures();
  (void)dsCoil.getTempCByIndex(0);

  // Warm-up DHT
  for (int i = 0; i < 10; i++) {
    float t1 = dht1.readTemperature();
    float t2 = dht2.readTemperature();
    if (!isnan(t1) && !isnan(t2)) break;
    delay(2000);
  }

  setupWifi();

  espClient.setInsecure();
  espClient.setTimeout(20);

  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(callback);
  client.setBufferSize(1024);
  client.setKeepAlive(15);

  Serial.println("System Ready.");
}

void loop() {
  checkConnection();

  if (client.connected()) {
    client.loop();
  }

  handleButtons();
  handleCalibrationState();

  unsigned long now = millis();

  if (now - lastSampleTime >= SAMPLE_INTERVAL &&
      calibState != CALIBBASELINEWAIT &&
      calibState != CALIBRUNNING) {

    lastSampleTime = now;

    if (applianceType == "Dryer") {
      float bt = bme.readTemperature();
      float bh = bme.readHumidity();
      float bp = bme.readPressure() / 100.0F;

      sumBME280T += isnan(bt) ? 0 : bt;
      sumBME280H += isnan(bh) ? 0 : bh;
      sumBME280P += isnan(bp) ? 0 : bp;
    } else {
      float d1t = dht1.readTemperature();
      float d1h = dht1.readHumidity();
      float d2t = dht2.readTemperature();
      float d2h = dht2.readHumidity();
      dsCoil.requestTemperatures();
      float dst = dsCoil.getTempCByIndex(0);

      sumDHT1T += isnan(d1t) ? 0 : d1t;
      sumDHT1H += isnan(d1h) ? 0 : d1h;
      sumDHT2T += isnan(d2t) ? 0 : d2t;
      sumDHT2H += isnan(d2h) ? 0 : d2h;
      sumDS18B20T += (dst < -50 || dst > 120 || isnan(dst)) ? 0 : dst;
    }

    double currentVal = readCurrentIrms();
    sumCurrentA += (isnan(currentVal) || currentVal <= 0.0) ? 0 : currentVal;

    sampleCount++;
  }

  if (sampleCount >= MAX_SAMPLES) {
    String basePayload = buildTelemetryPayload();

    resetAverages();

    if (client.connected()) {
      publishTelemetry(addAgeToPayload(basePayload, 0));

      while (!offlineQueue.empty()) {
        BufferedData item = offlineQueue.front();
        unsigned long ageMs = millis() - item.timestamp;
        publishTelemetry(addAgeToPayload(item.payload, ageMs));
        offlineQueue.erase(offlineQueue.begin());
        delay(120);
      }
    } else {
      if ((int)offlineQueue.size() >= MAX_QUEUE_SIZE) {
        offlineQueue.erase(offlineQueue.begin());
      }
      offlineQueue.push_back({basePayload, millis()});
    }
  }
}