"""
SQLite storage layer for processed driving data.

Stores trip summaries, per-frame motion metrics, and detected
driving events in three related tables.

Raw point clouds are not stored, only the processed results
are persisted to support trip analysis and historical queries.
"""

import sqlite3
from datetime import datetime, timezone

def create_tables(conn: sqlite3.Connection) -> None:
    """ 
    Create the database tables if they do not exist 
    Enables foreign key checks and creates tables for trips,
    frame-level metrics, and driving events.
    """

    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trips (
        trip_id INTEGER NOT NULL PRIMARY KEY,
        driver_id INTEGER NOT NULL,
        duration_sec REAL NOT NULL,
        recorded_at TEXT NOT NULL,  
        distance_m REAL NOT NULL CHECK (distance_m >= 0),
        avg_speed_kmh REAL,
        max_speed_kmh REAL,
        overall_score REAL,
        acceleration_score REAL,
        braking_score REAL,
        cornering_score REAL,
        rapid_acceleration_count INTEGER NOT NULL DEFAULT 0,
        harsh_braking_count INTEGER NOT NULL DEFAULT 0,
        hard_cornering_count INTEGER NOT NULL DEFAULT 0,
        source_scenario TEXT
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS frame_metrics (
        trip_id INTEGER NOT NULL,
        frame_id INTEGER NOT NULL,
        x REAL,  
        y REAL,
        speed_mps REAL,
        heading REAL,
        acceleration_mps2 REAL,
        lateral_acceleration_mps2 REAL,
        elapsed_sec REAL NOT NULL,

        -- Frame IDs are unique within each trip, not across all trips.
        PRIMARY KEY (trip_id, frame_id),
        FOREIGN KEY (trip_id) REFERENCES trips(trip_id)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS driving_events (
        event_id INTEGER NOT NULL PRIMARY KEY,
        trip_id INTEGER NOT NULL,
        event_type TEXT NOT NULL, 
        start_time_sec REAL NOT NULL,
        end_time_sec REAL NOT NULL,
        peak_value REAL NOT NULL,

        FOREIGN KEY (trip_id) REFERENCES trips(trip_id)
        );
    """)


def save_trip(conn: sqlite3.Connection, result: dict, driver_id: int, scenario: str) -> int:
    """ 
    Save a processed trip, its frame metrics, and driving events.

    Inserts the trip summary first to obtain its generated trip ID,
    then uses that ID to link all frames and events to the trip.

    The transaction is managed by the caller.

    Returns:
    The database ID assigned to the new trip.
    """

    if len(result["frame_metrics"]) != result["frame_count"]:
        raise ValueError("Frame count does not match the collected metrics")

    summary = result["summary"]

    recorded_at = datetime.now(timezone.utc).isoformat()

    cursor = conn.execute(
        """
        INSERT INTO trips (
            driver_id,
            duration_sec,
            recorded_at,  
            distance_m,
            avg_speed_kmh,
            max_speed_kmh,
            overall_score,
            acceleration_score,
            braking_score,
            cornering_score,
            rapid_acceleration_count,
            harsh_braking_count,
            hard_cornering_count,
            source_scenario
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            driver_id,
            summary["duration_sec"],
            recorded_at,
            summary["distance_m"],
            summary["avg_speed_kmh"], 
            summary["max_speed_kmh"],
            summary["overall_score"],
            summary["acceleration_score"],
            summary["braking_score"],
            summary["cornering_score"],
            summary["rapid_acceleration_count"],
            summary["harsh_braking_count"],
            summary["hard_cornering_count"],
            scenario
        )
    )

    # Get the database-generated ID to link frames and events to this trip
    trip_id = cursor.lastrowid

    frames = result["frame_metrics"]
    frame_rows = []

    # Convert the list of frame dictionaries into tuples matching
    # the SQL column order for batch insertion
    for frame in frames:
        frame_rows.append((
            trip_id,
            frame["frame_id"],
            frame["x"],
            frame["y"],
            frame["speed"],
            frame["heading"],
            frame["acceleration"],
            frame["lateral_acceleration"],
            frame["timestamp"]))

    conn.executemany(
    """
    INSERT INTO frame_metrics (
        trip_id,
        frame_id,
        x,
        y,
        speed_mps,
        heading,
        acceleration_mps2,
        lateral_acceleration_mps2,
        elapsed_sec
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
    frame_rows)

    events = result["events"]
    event_rows = []

    # Prepare event records for batch insertion using the same trip ID.
    for event in events:
        event_rows.append((
            trip_id,
            event["type"],
            event["start_time"],
            event["end_time"],
            event["peak_value"]
        ))

    conn.executemany(
        """
        INSERT INTO driving_events (
            trip_id,
            event_type,
            start_time_sec,
            end_time_sec,
            peak_value
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        event_rows
    )

    return trip_id
