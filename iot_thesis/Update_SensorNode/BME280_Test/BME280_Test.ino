#include <Wire.h>
#include <Adafruit_BME280.h>

Adafruit_BME280 bme;

void setup() {
  Serial.begin(115200);
  delay(500);
  Wire.begin(8, 9);

  Serial.println("Trying 0x76...");
  if (bme.begin(0x76)) {
    Serial.println("BME OK at 0x76");
  } else {
    Serial.println("Not at 0x76");
    Serial.println("Trying 0x77...");
    if (bme.begin(0x77)) {
      Serial.println("BME OK at 0x77");
    } else {
      Serial.println("Not at 0x77 either. Sensor is dead or wrong wiring.");
      return;
    }
  }
}

void loop() {
  float t = bme.readTemperature();
  float h = bme.readHumidity();
  float p = bme.readPressure() / 100.0;

  Serial.print("T="); Serial.print(t);
  Serial.print(" H="); Serial.print(h);
  Serial.print(" P="); Serial.println(p);

  delay(2000);
}
