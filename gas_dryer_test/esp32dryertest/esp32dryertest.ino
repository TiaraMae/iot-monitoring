#include <Wire.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <ArduinoMqttClient.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include "esp_adc_cal.h"

// --- WiFi ---
const char* ssid = "SGU-Guest"; 
const char* password = "Springbloom@2026"; 

// --- MQTT HiveMQ ---
const char mqtt_server[] = "d57bf82836a7485d9b67b270c681fe6e.s1.eu.hivemq.cloud";
const int mqtt_port = 8883;
const char mqtt_user[] = "esp32dryertest";
const char mqtt_pass[] = "Esp32dryertest";

// --- Pins ---
#define PINI2CSDA 8
#define PINI2CSCL 9
#define ADC_PIN 0      // SCT current sensor
#define LED_PIN 4      // LED indicator

Adafruit_BME280 bme;

// --- SCT Settings ---
const float CALIBRATION_FACTOR = 37.0; 
const float SCT_DEDUCTOR = 0.111;
const float RUNNING_THRESHOLD = 0.3; // Minimum Amps to consider the appliance "running"

esp_adc_cal_characteristics_t adc1_chars;
const int BUFFER_SIZE = 10;
double rmsBuffer[BUFFER_SIZE] = {0};
int bufferIndex = 0;
double runningTotal = 0;
bool isBufferFull = false;

// Status Flags
bool is_appliance_running = false;
bool was_appliance_running = false; // To detect when it turns on
float current_val = 0.0; 
bool wifi_ok = false;
bool mqtt_ok = false;
bool backend_ack = false; // Assume offline until proven otherwise!

// --- Offsets & Topics ---
float temp_offset = 0.0;
float rh_offset = 0.0;
const char pub_topic[] = "dryer/BME_TEST_01/telemetry";
const char sub_topic[] = "dryer/BME_TEST_01/ack";

// --- Averaging & Timing ---
const unsigned long sample_interval = 2000; // Read every 2 seconds
const int SAMPLES_NEEDED = 5;               // 5 samples * 2s = 10s average
const unsigned long heartbeat_interval = 30000;
const unsigned long ping_interval = 20000;  
unsigned long lastSample = 0;
unsigned long lastHeartbeat = 0;
unsigned long lastPing = 0;

// LED Blinking Timing
unsigned long lastLedToggle = 0;
bool ledState = false;

// Accumulators for averaging
int bme_sample_count = 0;
float sum_t = 0.0, sum_rh = 0.0, sum_p = 0.0, sum_current = 0.0;

WiFiClientSecure net;
MqttClient mqttClient(net);

// --- Offline Buffer ---
struct Reading {
  unsigned long timestamp_ms;
  float t, rh, p, current;
};
#define MAX_BUFFER 100
Reading buffer[MAX_BUFFER];
int buffer_head = 0;
int buffer_count = 0;
bool buffer_overflow = false;

void onMqttMessage(int messageSize) {
  String msg = "";
  while (mqttClient.available()) msg += (char)mqttClient.read();
  Serial.println("ACK: " + msg);
  backend_ack = true;
  lastHeartbeat = millis();
}

void addToBuffer(float t, float rh, float p, float current) {
  buffer[buffer_head] = {millis(), t, rh, p, current};
  buffer_head = (buffer_head + 1) % MAX_BUFFER;
  if (buffer_count < MAX_BUFFER) buffer_count++;
  else buffer_overflow = true;
}

void flushBuffer() {
  if (buffer_count == 0) return;
  Serial.printf("Flushing %d buffered items to database...\n", buffer_count);

  int idx = (buffer_head + MAX_BUFFER - buffer_count) % MAX_BUFFER;

  for (int i = 0; i < buffer_count; i++) {
    Reading r = buffer[idx];
    unsigned long ago_ms = millis() - r.timestamp_ms;

    char t_buf[10]; dtostrf(r.t, 4, 1, t_buf);
    char rh_buf[10]; dtostrf(r.rh, 4, 1, rh_buf);
    char c_buf[10]; dtostrf(r.current, 4, 2, c_buf); 

    mqttClient.beginMessage(pub_topic);
    mqttClient.print("{\"device\":\"BME_DRYER_01\",\"t_exhaust\":");
    mqttClient.print(t_buf);
    mqttClient.print(",\"rh_exhaust\":");
    mqttClient.print(rh_buf);
    mqttClient.print(",\"p_exhaust\":");
    mqttClient.print((int)r.p);
    mqttClient.print(",\"current\":");
    mqttClient.print(c_buf);
    mqttClient.print(",\"ago_ms\":");
    mqttClient.print(ago_ms);
    mqttClient.print("}"); 
    int result = mqttClient.endMessage();

    idx = (idx + 1) % MAX_BUFFER;
    if (!result) break;
    delay(50);
  }
  buffer_count = 0; 
  buffer_overflow = false;
  Serial.println("✅ Buffer flushed");
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  
   unsigned long startWait = millis();
  while (!Serial && millis() - startWait < 3000) {
    delay(10);
  }

  Serial.println("BME Dryer Buffer + SCT + Ping + Status Fix");

  // ADC config for SCT
  analogReadResolution(12);
  analogSetPinAttenuation(ADC_PIN, ADC_11db);
  esp_adc_cal_characterize(ADC_UNIT_1, ADC_ATTEN_DB_11, ADC_WIDTH_BIT_12, 1100, &adc1_chars);

  // I2C & BME
  Wire.begin(PINI2CSDA, PINI2CSCL);
  if (!bme.begin(0x76)) {
    Serial.println("❌ BME FAIL");
    while(1) {
      digitalWrite(LED_PIN, HIGH);
      delay(100);
      digitalWrite(LED_PIN, LOW);
      delay(200);
    }
  }
  Serial.println("✅ BME OK");

  bme.setSampling(Adafruit_BME280::MODE_NORMAL,
                  Adafruit_BME280::SAMPLING_X2,
                  Adafruit_BME280::SAMPLING_X16,
                  Adafruit_BME280::SAMPLING_X1,
                  Adafruit_BME280::FILTER_X16,
                  Adafruit_BME280::STANDBY_MS_62_5);

  // WiFi Setup
  WiFi.begin(ssid, password);
  WiFi.setTxPower(WIFI_POWER_8_5dBm);
  Serial.print("WiFi...");
  while (WiFi.status() != WL_CONNECTED) {
    digitalWrite(LED_PIN, HIGH); delay(500);
    digitalWrite(LED_PIN, LOW); delay(500);
    Serial.print(".");
  }
  wifi_ok = true;
  Serial.println("\n✅ WiFi: " + WiFi.localIP().toString());

  // MQTT Setup
  net.setInsecure();
  mqttClient.setId("ESP32_Dryer_BME");
  mqttClient.setUsernamePassword(mqtt_user, mqtt_pass);
  mqttClient.onMessage(onMqttMessage);

  Serial.print("MQTT...");
  while (!mqttClient.connect(mqtt_server, mqtt_port)) {
    digitalWrite(LED_PIN, HIGH); delay(500);
    digitalWrite(LED_PIN, LOW); delay(500);
    Serial.print(".");
  }
  mqtt_ok = true;
  Serial.println("\n✅ MQTT OK!");

  // Subscribe AFTER connecting
  mqttClient.subscribe(sub_topic);

  // Send Boot Ping
  mqttClient.beginMessage(pub_topic);
  mqttClient.print("{\"device\":\"BME_DRYER_01\",\"t_exhaust\":0,\"rh_exhaust\":0,\"p_exhaust\":0,\"current\":0,\"ago_ms\":0}"); 
  mqttClient.endMessage();
  Serial.println("📡 Sent boot ping to test backend connection...");

  buffer_count = 0;
  buffer_head = 0;
  lastPing = millis(); 
  lastHeartbeat = millis();
}

void loop() {
  wifi_ok = (WiFi.status() == WL_CONNECTED);
  mqtt_ok = mqttClient.connected();
  
  if (mqtt_ok) {
    mqttClient.poll();
  }

  // --- 1. SCT CURRENT LOGIC ---
  unsigned long startMillis = millis();
  long sum = 0;
  double sumSquared = 0;
  int count = 0;
  while (millis() - startMillis < 200) {
    long raw = analogRead(ADC_PIN);
    sum += raw;
    sumSquared += (raw * raw);
    count++;
  }
  float mean = (float)sum / count;
  float meanSquare = (float)sumSquared / count;
  float variance = meanSquare - (mean * mean);
  if (variance < 0) { variance = 0; }
  
  float rmsADC = sqrt(variance);
  uint32_t true_voltage_mV = esp_adc_cal_raw_to_voltage((uint32_t)rmsADC, &adc1_chars); 
  float rmsVoltage = true_voltage_mV / 1000.0;
  double current_Irms = (rmsVoltage * CALIBRATION_FACTOR);

  runningTotal -= rmsBuffer[bufferIndex];
  rmsBuffer[bufferIndex] = current_Irms;
  runningTotal += current_Irms;
  bufferIndex++;
  if (bufferIndex >= BUFFER_SIZE) {
    bufferIndex = 0;
    isBufferFull = true;
  }

  if (isBufferFull) {
    current_val = (runningTotal / BUFFER_SIZE) - SCT_DEDUCTOR;
    if (current_val < 0) current_val = 0; 
    
    is_appliance_running = (current_val >= RUNNING_THRESHOLD);
  }

  // --- STATE CHANGE ---
  if (is_appliance_running && !was_appliance_running) {
    Serial.println("🔥 Dryer turned ON! Probing backend...");
    if (mqtt_ok) {
      mqttClient.beginMessage(pub_topic);
      mqttClient.print("{\"device\":\"BME_DRYER_01\",\"t_exhaust\":0,\"rh_exhaust\":0,\"p_exhaust\":0,\"current\":0,\"ago_ms\":0}"); 
      mqttClient.endMessage();
    }
  }
  was_appliance_running = is_appliance_running;

  // --- IDLE PING ---
  if (!is_appliance_running && mqtt_ok) {
    if (millis() - lastPing > ping_interval) {
      mqttClient.beginMessage(pub_topic);
      mqttClient.print("{\"device\":\"BME_DRYER_01\",\"t_exhaust\":0,\"rh_exhaust\":0,\"p_exhaust\":0,\"current\":0,\"ago_ms\":0}"); 
      mqttClient.endMessage();
      Serial.println("📡 Sent idle ping to backend...");
      lastPing = millis();
    }
  }

  // --- STRICT BACKEND TIMEOUT ---
  if (millis() - lastHeartbeat > heartbeat_interval) backend_ack = false;
  bool all_ok = wifi_ok && mqtt_ok && backend_ack;

  // --- SMART RECONNECT LOGIC ---
  static unsigned long last_reconnect_attempt = 0;
  if (!wifi_ok) {
    if (millis() - last_reconnect_attempt > 10000) {
      Serial.println("🔄 Wi-Fi disconnected. Attempting reconnect...");
      WiFi.disconnect();
      WiFi.begin(ssid, password);
      last_reconnect_attempt = millis();
    }
  } else if (!mqtt_ok) {
    if (millis() - last_reconnect_attempt > 10000) {
      Serial.println("🔄 MQTT disconnected. Attempting reconnect...");
      if (mqttClient.connect(mqtt_server, mqtt_port)) {
        mqttClient.subscribe(sub_topic);
        Serial.println("✅ MQTT Reconnected!");
        lastHeartbeat = millis(); 
        backend_ack = true;
      }
      last_reconnect_attempt = millis();
    }
  }

  // --- LED Handler (FIXED) ---
  if (!all_ok) {
    digitalWrite(LED_PIN, LOW); 
  } else if (is_appliance_running) {
    digitalWrite(LED_PIN, HIGH); 
  } else {
    // Cleaner blink logic: 4.5s OFF, 0.5s ON
    unsigned long timeSinceToggle = millis() - lastLedToggle;
    if (ledState && timeSinceToggle >= 500) {
      ledState = false;
      lastLedToggle = millis();
      digitalWrite(LED_PIN, LOW);
    } else if (!ledState && timeSinceToggle >= 4500) {
      ledState = true;
      lastLedToggle = millis();
      digitalWrite(LED_PIN, HIGH);
    }
  }

  // --- 2. BME SAMPLING & AVERAGING LOGIC ---
  unsigned long now = millis();
  if (now - lastSample >= sample_interval) {
    lastSample = now;

    // --- ONLY Print Idle Current once every 10 seconds (5 samples) ---
    if (!is_appliance_running) {
       static int idle_counter = 0;
       idle_counter++;
       if (idle_counter >= SAMPLES_NEEDED) {
           Serial.printf("💤 Dryer Idle | Current SCT-013 Reading: %.3fA\n", current_val);
           idle_counter = 0;
       }
    }

    if (is_appliance_running) {
      float t = bme.readTemperature() + temp_offset;
      float rh = bme.readHumidity() + rh_offset;
      float p = bme.readPressure() / 100.0F;

      if (!isnan(t) && !isnan(rh) && !isnan(p)) {
        sum_t += t;
        sum_rh += rh;
        sum_p += p;
        sum_current += current_val; 
        bme_sample_count++;

        Serial.printf("⏳ Sample %d/%d | T:%.1f°C | RH:%.1f%% | P:%.0fhPa | I:%.2fA\n", 
              bme_sample_count, SAMPLES_NEEDED, t, rh, p, current_val);

        if (bme_sample_count >= SAMPLES_NEEDED) {
          float avg_t = sum_t / SAMPLES_NEEDED;
          float avg_rh = sum_rh / SAMPLES_NEEDED;
          float avg_p = sum_p / SAMPLES_NEEDED;
          float avg_current = sum_current / SAMPLES_NEEDED;

          // Always add to buffer first
          addToBuffer(avg_t, avg_rh, avg_p, avg_current);

          if (all_ok) {
            flushBuffer(); 
            Serial.printf("✅ LIVE AVG T:%.1f°C RH:%.1f%% P:%.0fhPa I:%.2fA\n", avg_t, avg_rh, avg_p, avg_current);
          } else {
            Serial.printf("💾 OFFLINE BUFFERING [%d/%d stored in RAM] -> T:%.1f°C I:%.2fA\n", 
                          buffer_count, MAX_BUFFER, avg_t, avg_current);
            
            if (mqtt_ok) {
               mqttClient.beginMessage(pub_topic);
               // Send zeros for probe so it doesn't duplicate in the DB
               mqttClient.print("{\"device\":\"BME_DRYER_01\",\"t_exhaust\":0,\"rh_exhaust\":0,\"p_exhaust\":0,\"current\":0,\"ago_ms\":0}"); 
               mqttClient.endMessage();
               Serial.println("📡 Sent probe to test if Python backend is awake...");
            }
          }

          bme_sample_count = 0;
          sum_t = 0.0; sum_rh = 0.0; sum_p = 0.0; sum_current = 0.0;
        }
      } else {
        Serial.println("❌ BME read NAN");
      }
    } else {
      if (bme_sample_count > 0) {
        Serial.println("🛑 Appliance stopped. Discarding partial samples.");
        bme_sample_count = 0;
        sum_t = 0.0; sum_rh = 0.0; sum_p = 0.0; sum_current = 0.0;
      }
    }
  }
}