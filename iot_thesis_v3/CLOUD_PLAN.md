# Cloud Deployment Plan for IoT Monitoring v2

> **Document created:** 2026-05-09  
> **Purpose:** Guide for deploying the v2 Flask backend to a cloud platform so the ESP32 sensor nodes can communicate 24/7 without keeping a local laptop powered on.

---

## Why Cloud Deployment?

The current setup runs Flask + PostgreSQL on your laptop. This has two problems:

1. **Laptop must stay ON 24/7** — If the laptop sleeps, shuts down, or disconnects from WiFi, the entire system stops working.
2. **Dynamic home IP** — Your home internet IP changes daily, making it impossible for external services to reach your laptop reliably.

The ESP32 sensor nodes communicate via **MQTT** (HiveMQ Cloud broker) to the Flask backend. For the backend to receive telemetry, process it, and store it in the database, the Flask app must be running continuously.

**Cloud deployment solves both problems:** the backend runs on a server that never sleeps, with a static URL and 24/7 availability.

---

## Cloud Platform Comparison

| Platform | Free Tier Sleep? | MQTT OK? | Best For | Notes |
|----------|-----------------|----------|----------|-------|
| **Fly.io** | ❌ Never sleeps | ✅ Yes | **Recommended** | Free tier VMs stay alive 24/7. Native support for persistent background threads (MQTT). |
| **Railway** | ❌ Never sleeps | ✅ Yes | Good alternative | $5/mo free credit. A small Flask app running 24/7 costs ~$2-3/month. |
| **Render** | ✅ Sleeps after 15 min | ❌ No | Avoid for MQTT | Free tier pauses your app when no HTTP requests arrive. MQTT messages are lost during sleep. |
| **PythonAnywhere** | ❌ Never sleeps | ⚠️ Maybe | Avoid | Free tier is always-on, but may block outbound MQTT port 8883. |
| **AWS EC2** | ❌ Never sleeps | ✅ Yes | Full control | Free 12-month tier. More complex setup. |

### Recommended: Fly.io
- Free tier includes 3 shared-cpu-1x VMs (256 MB RAM each)
- VMs **never sleep** — your MQTT listener stays alive 24/7
- Simple deployment: `fly deploy`
- Good documentation and CLI

---

## Database Options

### Option A: Migrate to Neon PostgreSQL (Recommended)

**Neon** is serverless PostgreSQL with a generous free tier:
- **500 MB storage per project**
- **100 CU-hours compute per month**
- **Free forever**, no credit card required

#### Will 10 Sensor Nodes Hit the Limit?

**Short answer: No, for a typical thesis project.**

One sensor reading row ≈ 100 bytes.

If all 10 nodes run **8 hours/day**:
```
10 nodes × 6 readings/min × 60 min × 8 hrs = 28,800 readings/day
28,800 × 100 bytes = ~2.9 MB/day
```

| Timeframe | Storage Used |
|-----------|-------------|
| 1 week | ~20 MB |
| 1 month | ~87 MB |
| 3 months | ~261 MB |
| 5 months | ~435 MB |
| **6 months** | **~522 MB** → Exceeds 500 MB limit |

**For a typical 2-3 month thesis:** ✅ Well within the 500 MB limit.

**If testing continuously for 6+ months:**
- Periodically delete old readings:  
  ```sql
  DELETE FROM dryer_readings WHERE time < NOW() - INTERVAL '3 months';
  DELETE FROM hvac_readings WHERE time < NOW() - INTERVAL '3 months';
  ```
- Or export to Excel and delete from DB
- Or upgrade to Neon Launch plan ($19/month → 10 GB storage)

#### Migrating Your Local Data to Neon

Your local laptop data does **NOT** automatically transfer. You must migrate it manually:

```bash
# Step 1: Export your local database
pg_dump -h localhost -U postgres -d iot_db > iot_db_backup.sql

# Step 2: Import into Neon
psql -h ep-xxx.us-east-1.aws.neon.tech -U your_neon_user -d iot_db < iot_db_backup.sql
```

**Your local DB is NOT deleted.** It stays on your laptop as a backup. Neon gets a copy.

#### Connecting Flask to Neon

1. Get your connection string from the Neon dashboard:
   ```
   postgresql://username:password@ep-xxx.us-east-1.aws.neon.tech/iot_db?sslmode=require
   ```
2. Set it as an environment variable on your cloud platform:
   ```
   NEON_DATABASE_URL=postgresql://username:password@ep-xxx.us-east-1.aws.neon.tech/iot_db?sslmode=require
   ```
3. Modify `app.py` to use the connection string if available:
   ```python
   import os
   import psycopg2
   
   db_url = os.getenv('NEON_DATABASE_URL') or os.getenv('DATABASE_URL')
   if db_url:
       conn = psycopg2.connect(db_url)
   else:
       conn = psycopg2.connect(
           host=os.getenv('DB_HOST', 'localhost'),
           port=os.getenv('DB_PORT', '5432'),
           database=os.getenv('DB_NAME', 'iot_db'),
           user=os.getenv('DB_USER', 'postgres'),
           password=os.getenv('DB_PASSWORD', 'IOTTHESIS')
       )
   ```

### Option B: Supabase PostgreSQL
- Same 500 MB storage limit as Neon
- Plus 1 GB file storage and 2 GB bandwidth
- Also free forever
- Connection string works the same way

### Option C: Keep Your Existing PostgreSQL

If your current PostgreSQL is on a server with a **public IP** (e.g., a university server, a VPS, or a cloud VM):
1. Whitelist the cloud platform's outbound IP in your Postgres firewall (`pg_hba.conf`)
2. Set the same `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` as environment variables on the cloud platform
3. Done — zero migration needed

**If your DB is only on your laptop:** This is NOT practical for cloud deployment. Your laptop must stay on 24/7, your home IP changes daily, and your router blocks incoming connections. See "Can I Keep Using My Laptop DB?" below.

---

## Can I Keep Using My Laptop DB with a Cloud Flask App?

**Technically yes, but practically no.**

| Problem | Why It Breaks |
|--------|--------------|
| Laptop must stay ON 24/7 | If it sleeps, the cloud app can't reach the DB |
| Dynamic home IP | Your internet IP changes daily; the cloud app loses connection |
| Home router firewall | Blocks incoming PostgreSQL port (5432) |
| Power outages | Any interruption kills the entire system |
| Slow upload speed | Home internet upload is much slower than download |

**Workaround (not recommended):** Use `ngrok` to tunnel your local Postgres:
```bash
ngrok tcp 5432
```
This gives you a temporary public URL. But it's fragile, the URL changes every restart, and your laptop still must stay on.

---

## Recommended Stack

| Component | Choice | Why |
|-----------|--------|-----|
| **Cloud Platform** | Fly.io | Free, never sleeps, MQTT stays alive |
| **Database** | Neon PostgreSQL (free tier) | 500 MB is plenty for 2-3 months of thesis data |
| **MQTT Broker** | Keep HiveMQ Cloud | Already working, no changes needed |
| **App Server** | Gunicorn | Production WSGI server |

---

## Environment Variables Needed on Cloud

Set these in your cloud platform's dashboard (Fly.io secrets, Railway variables, etc.):

```env
FLASK_SECRET_KEY=your-secret-key-here
MQTT_HOST=your-hivemq-broker.hivemq.cloud
MQTT_PORT=8883
MQTT_USER=your-mqtt-username
MQTT_PASS=your-mqtt-password

# Option A: Neon
NEON_DATABASE_URL=postgresql://user:pass@ep-xxx.neon.tech/iot_db?sslmode=require

# Option B: Existing DB
DB_HOST=your-db-host
DB_PORT=5432
DB_NAME=iot_db
DB_USER=postgres
DB_PASSWORD=your-password
```

**Important:** Do NOT commit `.env` to Git. Set these as environment variables on the cloud platform.

---

## Step-by-Step Deployment Guide (Fly.io)

### 1. Install Fly.io CLI
```bash
powershell -Command "iwr https://fly.io/install.ps1 -useb | iex"
```

### 2. Login
```bash
fly auth login
```

### 3. Create App
```bash
cd iot_thesis_v2
fly apps create your-app-name
```

### 4. Set Secrets (Environment Variables)
```bash
fly secrets set FLASK_SECRET_KEY="your-secret"
fly secrets set MQTT_HOST="your-broker.hivemq.cloud"
fly secrets set MQTT_USER="your-user"
fly secrets set MQTT_PASS="your-pass"
fly secrets set NEON_DATABASE_URL="postgresql://..."
```

### 5. Create `fly.toml`
```toml
app = "your-app-name"
primary_region = "sin"  # Singapore (closest to Indonesia)

[build]
  builder = "paketobuildpacks/builder:base"

[env]
  PORT = "5000"

[[services]]
  internal_port = 5000
  protocol = "tcp"
  auto_stop_machines = false
  auto_start_machines = true
  min_machines_running = 1

  [[services.ports]]
    port = 80
    handlers = ["http"]

  [[services.ports]]
    port = 443
    handlers = ["tls", "http"]
```

### 6. Deploy
```bash
fly deploy
```

### 7. Open Dashboard
```bash
fly open
```

---

## ESP32 Firmware Changes

**No firmware changes needed.** The ESP32 connects directly to HiveMQ Cloud MQTT broker — it does not connect to the Flask backend URL. The only URL in your browser changes (from `127.0.0.1:5000` to `https://your-app.fly.dev`), but the MQTT broker stays the same.

---

## Data Retention Strategy

To stay under Neon's 500 MB limit during long-term testing:

1. **Export old data monthly:**
   - Use the "Export to Excel" button in the dashboard
   - Save the `.xlsx` file as a backup

2. **Delete old readings from DB:**
   ```sql
   DELETE FROM dryer_readings WHERE time < NOW() - INTERVAL '2 months';
   DELETE FROM hvac_readings WHERE time < NOW() - INTERVAL '2 months';
   DELETE FROM alerts WHERE created_at < NOW() - INTERVAL '2 months';
   ```

3. **Keep sensor_events and maintenance history** — these are small and valuable for tracking.

---

## Summary

| Question | Answer |
|----------|--------|
| Will Render free tier work? | ❌ No — it sleeps and loses MQTT messages |
| Will 10 nodes hit Neon free tier? | ❌ Not for 2-3 months of thesis testing |
| Do I lose my local data? | ❌ No — migrate with `pg_dump`, local stays as backup |
| Must laptop stay on? | ❌ Not if you deploy to cloud + Neon |
| Do I change firmware? | ❌ No — MQTT broker stays the same |
| Easiest path? | ✅ Fly.io + Neon, migrate data with `pg_dump` |
