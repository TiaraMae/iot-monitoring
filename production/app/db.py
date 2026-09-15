"""PostgreSQL connection pool and idempotent startup migrations."""

import psycopg2
import psycopg2.pool

from app import config


try:
    db_pool = psycopg2.pool.SimpleConnectionPool(
        1,
        10,
        host=config.DB_HOST,
        port=config.DB_PORT,
        dbname=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
    )
except Exception as e:
    print(f"DB Pool Error: {e}")
    db_pool = None


def get_conn():
    try:
        if db_pool:
            return db_pool.getconn()
    except Exception as e:
        print(f"DB Get Connection Error: {e}")
    return None


def release_conn(conn):
    if db_pool and conn:
        db_pool.putconn(conn)


def _run_startup_migrations():
    """Run lightweight idempotent migrations on startup."""
    conn = get_conn()
    if not conn:
        return
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                discord_webhook_url TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS appliances (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                sub_type TEXT,
                is_inverter BOOLEAN DEFAULT FALSE,
                location TEXT DEFAULT 'Home',
                brand TEXT DEFAULT 'Generic',
                operational_status TEXT DEFAULT 'offset_calibration_needed',
                baseline_configured BOOLEAN DEFAULT FALSE,
                alert_enabled BOOLEAN DEFAULT TRUE,
                voltage REAL DEFAULT 220.0,
                cf REAL,
                deductor REAL,
                treturn_offset REAL DEFAULT 0.0,
                tsupply_offset REAL DEFAULT 0.0,
                delta_t_lcl REAL,
                delta_t_delay_minutes INTEGER,
                atmospheric_pressure REAL,
                map_x REAL,
                map_y REAL,
                map_w REAL DEFAULT 120.0,
                map_h REAL DEFAULT 60.0,
                map_on_map BOOLEAN DEFAULT FALSE,
                current_sensor TEXT,
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sensor_nodes (
                id SERIAL PRIMARY KEY,
                mac_address TEXT UNIQUE NOT NULL,
                status TEXT DEFAULT 'unpaired',
                appliance_id INTEGER REFERENCES appliances(id),
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                last_seen TIMESTAMP WITHOUT TIME ZONE
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hvac_readings (
                id SERIAL PRIMARY KEY,
                sensor_node_id INTEGER NOT NULL REFERENCES sensor_nodes(id),
                time TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                treturn REAL,
                tsupply REAL,
                icompressor REAL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dryer_readings (
                id SERIAL PRIMARY KEY,
                sensor_node_id INTEGER NOT NULL REFERENCES sensor_nodes(id),
                time TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                texhaust REAL,
                rh_exhaust REAL,
                pressure REAL,
                abs_pressure REAL,
                imotor REAL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS spc_manual_baselines (
                appliance_id INTEGER REFERENCES appliances(id),
                metric_name TEXT NOT NULL,
                ucl REAL,
                lcl REAL,
                mean REAL,
                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                PRIMARY KEY (appliance_id, metric_name)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id SERIAL PRIMARY KEY,
                appliance_id INTEGER REFERENCES appliances(id),
                alert_type TEXT NOT NULL,
                message TEXT,
                value REAL,
                threshold REAL,
                severity TEXT DEFAULT 'warning',
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                resolved_at TIMESTAMP WITHOUT TIME ZONE,
                acknowledged BOOLEAN DEFAULT FALSE
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sensor_events (
                id SERIAL PRIMARY KEY,
                sensor_node_mac TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timestamp TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
            )
        """)

        # Idempotent column additions in case the schema evolves
        columns = [
            ("users", "discord_webhook_url", "TEXT"),
            ("appliances", "sub_type", "TEXT"),
            ("appliances", "is_inverter", "BOOLEAN DEFAULT FALSE"),
            ("appliances", "baseline_configured", "BOOLEAN DEFAULT FALSE"),
            ("appliances", "alert_enabled", "BOOLEAN DEFAULT TRUE"),
            ("appliances", "voltage", "REAL DEFAULT 220.0"),
            ("appliances", "cf", "REAL"),
            ("appliances", "deductor", "REAL"),
            ("appliances", "treturn_offset", "REAL DEFAULT 0.0"),
            ("appliances", "tsupply_offset", "REAL DEFAULT 0.0"),
            ("appliances", "delta_t_lcl", "REAL"),
            ("appliances", "delta_t_delay_minutes", "INTEGER"),
            ("appliances", "atmospheric_pressure", "REAL"),
            ("appliances", "map_x", "REAL"),
            ("appliances", "map_y", "REAL"),
            ("appliances", "map_w", "REAL DEFAULT 120.0"),
            ("appliances", "map_h", "REAL DEFAULT 60.0"),
            ("appliances", "map_on_map", "BOOLEAN DEFAULT FALSE"),
            ("appliances", "current_sensor", "TEXT"),
            ("appliances", "updated_at", "TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()"),
            ("sensor_nodes", "last_seen", "TIMESTAMP WITHOUT TIME ZONE"),
            ("dryer_readings", "abs_pressure", "REAL"),
            ("alerts", "severity", "TEXT DEFAULT 'warning'"),
            ("alerts", "acknowledged", "BOOLEAN DEFAULT FALSE"),
        ]
        for table, column, dtype in columns:
            cur.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {dtype}"
            )

        # Widen operational_status to support longer status labels.
        cur.execute("ALTER TABLE appliances ALTER COLUMN operational_status TYPE TEXT")

        # Ensure SPC baseline limit columns are nullable so partial baselines
        # (e.g. Delta-T LCL-only) can be saved. Older DBs may have NOT NULL
        # constraints from an earlier schema version.
        cur.execute("ALTER TABLE spc_manual_baselines ALTER COLUMN ucl DROP NOT NULL")
        cur.execute("ALTER TABLE spc_manual_baselines ALTER COLUMN lcl DROP NOT NULL")
        cur.execute("ALTER TABLE spc_manual_baselines ALTER COLUMN mean DROP NOT NULL")

        # Device-type rename: 'Air Conditioner'/'Dryer' -> 'HVAC'/'Gas Dryer'.
        cur.execute("UPDATE appliances SET type = 'HVAC' WHERE type = 'Air Conditioner'")
        cur.execute("UPDATE appliances SET type = 'Gas Dryer' WHERE type = 'Dryer'")

        # Backfill current_sensor for rows paired before the sensor picker
        # existed: CF 33.0 was the SCT-013-class clamp, anything else ZHT103C.
        cur.execute("""
            UPDATE appliances
            SET current_sensor = CASE WHEN cf = 33.0 THEN 'SCT013-015' ELSE 'ZHT103C' END
            WHERE current_sensor IS NULL
        """)

        conn.commit()
    except Exception as e:
        print(f"Startup migration error: {e}")
        conn.rollback()
    finally:
        cur.close()
        release_conn(conn)


_run_startup_migrations()
