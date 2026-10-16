
import sqlite3
from datetime import datetime, timezone


def create_tables(conn: sqlite3.Connection) -> None:
    """ Create the storage schema for ingestion and analytics """

    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trips (
            trip_id INTEGER PRIMARY KEY,
            driver_id INTEGER NOT NULL,
            recorded_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ingested'
                CHECK (status IN ('ingested', 'analyzed', 'failed')),
            error_code TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS frame_positions (
            trip_id INTEGER NOT NULL,
            frame_id INTEGER NOT NULL,
            elapsed_sec REAL NOT NULL CHECK (elapsed_sec >= 0),
            x REAL NOT NULL,
            y REAL NOT NULL,
            z REAL NOT NULL,

            PRIMARY KEY (trip_id, frame_id),
            FOREIGN KEY (trip_id) REFERENCES trips(trip_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS frame_metrics (
            trip_id INTEGER NOT NULL,
            frame_id INTEGER NOT NULL,
            speed_mps REAL,
            heading REAL,
            acceleration_mps2 REAL,
            lateral_acceleration_mps2 REAL,

            PRIMARY KEY (trip_id, frame_id),
            FOREIGN KEY (trip_id, frame_id)
                REFERENCES frame_positions(trip_id, frame_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS driving_events (
            event_id INTEGER PRIMARY KEY,
            trip_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            start_time_sec REAL NOT NULL,
            end_time_sec REAL NOT NULL,
            peak_value REAL NOT NULL,

            FOREIGN KEY (trip_id) REFERENCES trips(trip_id),
            CHECK (end_time_sec >= start_time_sec)
        )
    """)


def save_positions(conn: sqlite3.Connection, driver_id: int, positions: list[dict]) -> int:
    """ Save a complete trip's extracted positions """

    if not positions:
        raise ValueError("Cannot save a trip without frames")

    cursor = conn.execute(
        """
        INSERT INTO trips (driver_id, recorded_at, status)
        VALUES (?, ?, 'ingested')
        """,
        (driver_id, datetime.now(timezone.utc).isoformat())
    )

    trip_id = cursor.lastrowid

    rows = [
        (
            trip_id,
            frame["frame_id"],
            frame["timestamp"],
            frame["x"],
            frame["y"],
            frame["z"]
        )
        for frame in positions
    ]

    conn.executemany(
        """
        INSERT INTO frame_positions (
            trip_id, frame_id, elapsed_sec, x, y, z
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows
    )

    return trip_id
