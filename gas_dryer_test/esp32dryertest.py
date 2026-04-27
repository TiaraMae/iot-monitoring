#!/usr/bin/env python3
"""
BME Dryer MQTT + PostgreSQL + LIVE DASHBOARD 
Includes Date/Time Range Filter, Pressure Chart, Scrollable Data Table, and Excel Export
"""

import json
import paho.mqtt.client as mqtt
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone, timedelta
import os
import threading
import socket
from dotenv import load_dotenv
from flask import Flask, render_template_string, jsonify, request, send_file
import pandas as pd
import io

load_dotenv()

# === FLASK APP ===
app = Flask(__name__)

# === DYNAMIC IP DETECTION ===
def get_local_ip():
    """Dynamically get the current local Wi-Fi IP address"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

LOCAL_IP = get_local_ip()

# === CONFIG ===
MQTT_HOST = os.getenv("MQTT_HOST", "d57bf82836a7485d9b67b270c681fe6e.s1.eu.hivemq.cloud")
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))
MQTT_USER = os.getenv("MQTT_USER", "esp32dryertest")
MQTT_PASS = os.getenv("MQTT_PASS", "Esp32dryertest")

SUB_TOPIC = "dryer/BME_TEST_01/telemetry"
ACK_TOPIC = "dryer/BME_TEST_01/ack"

# Local Timezone offset for Indonesia (WIB = UTC+7)
LOCAL_TZ = timezone(timedelta(hours=7))

# === DB ===
def get_db_conn():
    # NEON CLOUD DATABASE CONNECTION
    NEON_URL = os.getenv(
        "NEON_DATABASE_URL",
        "postgresql://neondb_owner:npg_KldVwY87eSBi@ep-late-firefly-a1f2ll4t-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    )
    return psycopg2.connect(NEON_URL)

def init_db():
    conn = get_db_conn()
    cur = conn.cursor()
    # Changed table to v2 to prevent schema clashes with old 'rssi' column
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dryer_bme_readings_v2 (
            id SERIAL PRIMARY KEY,
            received_at TIMESTAMPTZ DEFAULT NOW(),
            device_id TEXT,
            t_exhaust REAL,
            rh_exhaust REAL,
            p_exhaust REAL,
            current REAL,
            ago_ms BIGINT DEFAULT 0,
            raw_json JSONB
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("✅ DB ready (dryer_bme_readings_v2 on Neon Cloud!)")

# === MQTT HANDLERS ===
def on_connect(client, userdata, flags, rc, properties=None):
    print(f"✅ MQTT Connected (rc={rc})")
    client.subscribe(SUB_TOPIC, qos=1)

def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode('utf-8')
        data = json.loads(payload)

        # Check if this is an IDLE PING from the ESP32 (all zeros)
        t_exhaust = float(data.get('t_exhaust', 0))

        # ONLY insert into database if it is real data (not a ping)
        if t_exhaust != 0:
            ago_ms = data.get('ago_ms', 0) or 0
            received_at = datetime.now(timezone.utc) - timedelta(milliseconds=ago_ms)

            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO dryer_bme_readings_v2 
                (received_at, device_id, t_exhaust, rh_exhaust, p_exhaust, current, ago_ms, raw_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                received_at.isoformat(),
                data.get('device'), 
                t_exhaust,
                float(data.get('rh_exhaust', 0)),
                float(data.get('p_exhaust', 0)),
                float(data.get('current', 0.0)),
                int(ago_ms),
                json.dumps(data)
            ))
            conn.commit()
            cur.close()
            conn.close()
            print(f"💾 Saved Data: T={t_exhaust}°C")
        else:
            print("🔄 Received Idle Ping. Sending ACK...")

        # ALWAYS send ACK back, whether it was real data or just a ping
        ack = {"ack": "ok", "count": 1, "ts": datetime.now(timezone.utc).isoformat()}
        client.publish(ACK_TOPIC, json.dumps(ack), qos=1)

    except Exception as e:
        print(f"❌ Error: {e}")

# === FLASK ROUTES ===
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>BME test Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: Arial, sans-serif; background-color: #f4f7f6; margin: 0; padding: 20px; }
        .header { text-align: center; margin-bottom: 20px; }
        .controls { display: flex; justify-content: center; gap: 10px; margin-bottom: 20px; background: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); flex-wrap: wrap; align-items: center; }
        .cards { display: flex; justify-content: space-around; margin-bottom: 20px; }
        .card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); text-align: center; width: 20%; }
        .card h3 { margin: 0 0 10px 0; color: #555; }
        .card h2 { margin: 0; color: #2c3e50; font-size: 2em; }
        .chart-container { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); margin-bottom: 20px; }
        input, button, select { padding: 8px; border-radius: 4px; border: 1px solid #ccc; font-size: 14px;}
        button { background-color: #3498db; color: white; border: none; cursor: pointer; }
        button:hover { background-color: #2980b9; }
        .btn-excel { background-color: #2ecc71; margin-left: 20px; }
        .btn-excel:hover { background-color: #27ae60; }

        /* Table Styles */
        .table-container { 
            background: white; 
            padding: 20px; 
            border-radius: 8px; 
            box-shadow: 0 4px 6px rgba(0,0,0,0.1); 
            max-height: 400px; 
            overflow-y: auto; 
        }
        table { width: 100%; border-collapse: collapse; text-align: left; }
        th { background-color: #3498db; color: white; padding: 12px; position: sticky; top: 0; z-index: 1; }
        td { padding: 10px; border-bottom: 1px solid #ddd; color: #333; }
        tr:hover { background-color: #f1f1f1; }
    </style>
</head>
<body>

    <div class="header">
        <h1>BME test Dashboard</h1>
    </div>

    <!-- Controls -->
    <div class="controls">
        <select id="mode" onchange="toggleMode()">
            <option value="live">Live Auto-Update</option>
            <option value="history">Historical Range</option>
        </select>

        <div id="date_inputs" style="display:none; gap:10px;">
            <label>From: <input type="datetime-local" id="start_time" step="1"></label>
            <label>To: <input type="datetime-local" id="end_time" step="1"></label>
            <button onclick="fetchHistory()">Apply Filter</button>
        </div>

        <button class="btn-excel" onclick="downloadExcel()">📥 Export to Excel</button>
    </div>

    <!-- Summary Cards -->
    <div class="cards">
        <div class="card">
            <h3>Temperature</h3>
            <h2 id="live_t">-- °C</h2>
        </div>
        <div class="card">
            <h3>Humidity</h3>
            <h2 id="live_rh">-- %</h2>
        </div>
        <div class="card">
            <h3>Pressure</h3>
            <h2 id="live_p">-- hPa</h2>
        </div>
        <div class="card">
            <h3>Current</h3>
            <h2 id="live_current">-- A</h2>
        </div>
    </div>

    <!-- Charts -->
    <div class="chart-container">
        <canvas id="t_rh_chart" height="80"></canvas>
    </div>

    <div class="chart-container">
        <canvas id="current_chart" height="60"></canvas>
    </div>

    <div class="chart-container">
        <canvas id="p_chart" height="60"></canvas>
    </div>

    <!-- Data Table -->
    <div class="table-container">
        <h3 style="margin-top:0; color:#555;">Data Log (Newest First)</h3>
        <table>
            <thead>
                <tr>
                    <th>Timestamp (WIB)</th>
                    <th>Temperature (°C)</th>
                    <th>Humidity (%)</th>
                    <th>Pressure (hPa)</th>
                    <th>Current (A)</th>
                </tr>
            </thead>
            <tbody id="table_body">
                <!-- Rows populated by JS -->
            </tbody>
        </table>
    </div>

    <script>
        // T&RH Chart
        const ctxTRH = document.getElementById('t_rh_chart').getContext('2d');
        const tRhChart = new Chart(ctxTRH, {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    { label: 'Temperature (°C)', borderColor: '#e74c3c', backgroundColor: 'rgba(231, 76, 60, 0.1)', data: [], fill: true, tension: 0.3, yAxisID: 'y' },
                    { label: 'Humidity (%)', borderColor: '#3498db', backgroundColor: 'rgba(52, 152, 219, 0.1)', data: [], fill: true, tension: 0.3, yAxisID: 'y1' }
                ]
            },
            options: {
                responsive: true,
                scales: {
                    x: { title: { display: true, text: 'Time' } },
                    y: { type: 'linear', display: true, position: 'left', title: { display: true, text: 'Temp (°C)' }, min: 20, max: 90 },
                    y1: { type: 'linear', display: true, position: 'right', title: { display: true, text: 'Humidity (%)' }, grid: { drawOnChartArea: false }, min: 0, max: 100 }
                },
                animation: { duration: 0 } 
            }
        });

        // Pressure Chart
        const ctxP = document.getElementById('p_chart').getContext('2d');
        const pChart = new Chart(ctxP, {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    { label: 'Pressure (hPa)', borderColor: '#2ecc71', backgroundColor: 'rgba(46, 204, 113, 0.1)', data: [], fill: true, tension: 0.3 }
                ]
            },
            options: {
                responsive: true,
                scales: {
                    x: { title: { display: true, text: 'Time' } },
                    y: { title: { display: true, text: 'Pressure (hPa)' }, min: 900, max: 1050 }
                },
                animation: { duration: 0 }
            }
        });

        // Current Chart
        const ctxC = document.getElementById('current_chart').getContext('2d');
        const cChart = new Chart(ctxC, {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    { label: 'Current (A)', borderColor: '#9b59b6', backgroundColor: 'rgba(155, 89, 182, 0.1)', data: [], fill: true, tension: 0.3 }
                ]
            },
            options: {
                responsive: true,
                scales: {
                    x: { title: { display: true, text: 'Time' } },
                    y: { title: { display: true, text: 'Current (A)' }, min: 0, max: 4 }
                },
                animation: { duration: 0 }
            }
        });

        let liveInterval;

        function updateUI(data) {
            if (!data || !data.labels || data.labels.length === 0) {
                // Remove alert to prevent popups on empty fresh DBs
                return;
            }

            // Update Cards
            if (data.latest) {
                document.getElementById('live_t').innerText = data.latest.t_exhaust.toFixed(1) + ' °C';
                document.getElementById('live_rh').innerText = data.latest.rh_exhaust.toFixed(1) + ' %';
                document.getElementById('live_p').innerText = data.latest.p_exhaust + ' hPa';
                document.getElementById('live_current').innerText = data.latest.current.toFixed(2) + ' A';
            }

            // Update Charts
            tRhChart.data.labels = data.labels;
            tRhChart.data.datasets[0].data = data.temperature;
            tRhChart.data.datasets[1].data = data.humidity;
            tRhChart.update();

            pChart.data.labels = data.labels;
            pChart.data.datasets[0].data = data.pressure;
            pChart.update();

            if (data.current) {
                cChart.data.labels = data.labels;
                cChart.data.datasets[0].data = data.current;
                cChart.update();
            }

            // Update Table
            const tbody = document.getElementById('table_body');
            tbody.innerHTML = ''; 

            for (let i = 0; i < data.labels.length; i++) {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${data.labels[i]}</td>
                    <td>${data.temperature[i].toFixed(2)}</td>
                    <td>${data.humidity[i].toFixed(2)}</td>
                    <td>${data.pressure[i]}</td>
                    <td>${data.current[i].toFixed(2)}</td>
                `;
                tbody.prepend(tr);
            }
        }

        function fetchLive() {
            fetch('/api/data')
                .then(response => response.json())
                .then(data => updateUI(data))
                .catch(err => console.log("Fetch Error: ", err));
        }

        function fetchHistory() {
            const start = document.getElementById('start_time').value;
            const end = document.getElementById('end_time').value;
            if(!start || !end) return alert("Please select both start and end times.");

            const safeStart = encodeURIComponent(start);
            const safeEnd = encodeURIComponent(end);

            fetch(`/api/data?start=${safeStart}&end=${safeEnd}`)
                .then(response => response.json())
                .then(data => updateUI(data))
                .catch(err => alert("Error fetching historical data."));
        }

        function toggleMode() {
            const mode = document.getElementById('mode').value;
            if (mode === 'live') {
                document.getElementById('date_inputs').style.display = 'none';
                fetchLive();
                liveInterval = setInterval(fetchLive, 3000);
            } else {
                document.getElementById('date_inputs').style.display = 'flex';
                clearInterval(liveInterval); // Stop auto-refresh
            }
        }

        function downloadExcel() {
            const mode = document.getElementById('mode').value;
            let url = '/api/download_excel';

            if (mode === 'history') {
                const start = document.getElementById('start_time').value;
                const end = document.getElementById('end_time').value;
                if (start && end) {
                    const safeStart = encodeURIComponent(start);
                    const safeEnd = encodeURIComponent(end);
                    url += `?start=${safeStart}&end=${safeEnd}`;
                } else {
                    alert("Please select dates first, or switch to Live mode to download everything.");
                    return;
                }
            }

            window.location.href = url;
        }

        toggleMode();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/data')
def get_data():
    start = request.args.get('start')
    end = request.args.get('end')

    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if start and end:
        try:
            start_dt = datetime.strptime(start, "%Y-%m-%dT%H:%M:%S") if len(start) > 16 else datetime.strptime(start, "%Y-%m-%dT%H:%M")
            end_dt = datetime.strptime(end, "%Y-%m-%dT%H:%M:%S") if len(end) > 16 else datetime.strptime(end, "%Y-%m-%dT%H:%M")
            start_utc = start_dt.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)
            end_utc = end_dt.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)

            # Filter out any leftover 0s from the database query just in case!
            cur.execute("""
                SELECT received_at, t_exhaust, rh_exhaust, p_exhaust, current 
                FROM dryer_bme_readings_v2 
                WHERE received_at >= %s AND received_at <= %s AND t_exhaust > 0
                ORDER BY received_at ASC
            """, (start_utc, end_utc))
            rows = cur.fetchall()
        except Exception as e:
            print("Date parse error:", e)
            rows = []
    else:
        # Filter out any leftover 0s from the database query just in case!
        cur.execute("""
            SELECT received_at, t_exhaust, rh_exhaust, p_exhaust, current 
            FROM dryer_bme_readings_v2 
            WHERE t_exhaust > 0
            ORDER BY received_at DESC 
            LIMIT 50
        """)
        rows = cur.fetchall()
        rows.reverse() 

    cur.close()
    conn.close()

    labels = []
    for r in rows:
        dt = r['received_at']
        if dt.tzinfo is not None:
            dt = dt.astimezone(LOCAL_TZ)
        labels.append(dt.strftime("%Y-%m-%d %H:%M:%S"))

    data = {
        "labels": labels,
        "temperature": [r['t_exhaust'] for r in rows],
        "humidity": [r['rh_exhaust'] for r in rows],
        "pressure": [r['p_exhaust'] for r in rows],
        "current": [r['current'] for r in rows]
    }

    if rows:
        data["latest"] = rows[-1]
    else:
        data["latest"] = {"t_exhaust": 0, "rh_exhaust": 0, "p_exhaust": 0, "current": 0}

    return jsonify(data)

@app.route('/api/download_excel')
def download_excel():
    start = request.args.get('start')
    end = request.args.get('end')

    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if start and end:
        try:
            start_dt = datetime.strptime(start, "%Y-%m-%dT%H:%M:%S") if len(start) > 16 else datetime.strptime(start, "%Y-%m-%dT%H:%M")
            end_dt = datetime.strptime(end, "%Y-%m-%dT%H:%M:%S") if len(end) > 16 else datetime.strptime(end, "%Y-%m-%dT%H:%M")
            start_utc = start_dt.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)
            end_utc = end_dt.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)

            cur.execute("""
                SELECT received_at, device_id, t_exhaust, rh_exhaust, p_exhaust, current 
                FROM dryer_bme_readings_v2 
                WHERE received_at >= %s AND received_at <= %s AND t_exhaust > 0
                ORDER BY received_at ASC
            """, (start_utc, end_utc))
            rows = cur.fetchall()
            filename = f"BME_Data_{start_dt.strftime('%Y%m%d_%H%M')}_to_{end_dt.strftime('%Y%m%d_%H%M')}.xlsx"
        except Exception as e:
            print("Date parse error for Excel:", e)
            rows = []
            filename = "BME_Data_Error.xlsx"
    else:
        cur.execute("""
            SELECT received_at, device_id, t_exhaust, rh_exhaust, p_exhaust, current 
            FROM dryer_bme_readings_v2 
            WHERE t_exhaust > 0
            ORDER BY received_at ASC 
        """)
        rows = cur.fetchall()
        filename = f"BME_Data_Full_Export_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"

    cur.close()
    conn.close()

    formatted_data = []
    for r in rows:
        dt = r['received_at']
        if dt.tzinfo is not None:
            dt = dt.astimezone(LOCAL_TZ)

        formatted_data.append({
            "Timestamp (WIB)": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "Device ID": r['device_id'],
            "Temperature (°C)": r['t_exhaust'],
            "Humidity (%)": r['rh_exhaust'],
            "Pressure (hPa)": r['p_exhaust'],
            "Current (A)": r['current']
        })

    df = pd.DataFrame(formatted_data)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Dryer Data')
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

def run_mqtt():
    client = mqtt.Client(client_id="dryer_bme_backend")
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set()
    client.on_connect = on_connect
    client.on_message = on_message
    print("🔌 MQTT Connecting...")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_forever()

if __name__ == "__main__":
    init_db()

    mqtt_thread = threading.Thread(target=run_mqtt, daemon=True)
    mqtt_thread.start()

    print(f"\n🚀 Flask Dashboard running!")
    print(f"🌍 Access it from ANY device on your Wi-Fi at: http://{LOCAL_IP}:5000\n")

    app.run(host='0.0.0.0', port=5000, debug=False)
