import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np

from database import create_tables
from errors import PipelineError
from transform import EventDetector, MotionProcessor
from sql_views import create_views

DB_PATH = Path(__file__).with_name("cloud.db")


def load_validated_positions(conn: sqlite3.Connection, trip_id: int) -> list[tuple]:
    """ Validate stored positions before either analysis mode """

    rows = conn.execute(
        """SELECT frame_id, elapsed_sec, x, y, z
           FROM frame_positions WHERE trip_id = ? ORDER BY frame_id""",
        (trip_id,),
    ).fetchall()
    if not rows:
        raise PipelineError("VL-201", f"No stored positions for trip {trip_id}")

    if len(rows) < 3:
        raise PipelineError("VL-204", f"Trip {trip_id}: at least three frames are required")

    for index, (frame_id, timestamp, x, y, z) in enumerate(rows):
        if frame_id != index:
            raise PipelineError("VL-205", "Frame IDs must be consecutive and start at zero")
        if not is_finite_number(timestamp) or timestamp < 0:
            raise PipelineError("VL-202", f"Invalid timestamp in frame {frame_id}")
        if index:
            dt = timestamp - rows[index - 1][1]
            if not np.isfinite(dt) or dt <= 0:
                raise PipelineError("VL-202", "Timestamps must strictly increase")
        if not all(is_finite_number(value) for value in (x, y, z)):
            raise PipelineError("VL-205", f"Invalid coordinates in frame {frame_id}")
    return rows


def is_finite_number(value) -> bool:
    """ Reject NULL, text, NaN and infinity without leaking a TypeError """

    return isinstance(value, (int, float)) and not isinstance(value, bool) and bool(np.isfinite(value))


def validate_metrics(rows: list[tuple]) -> None:
    """ Validate calculated or stored metrics, including expected initial NULLs """

    previous_heading = None
    for index, (_, frame_id, timestamp, speed, heading, acceleration, lateral) in enumerate(rows):
        values = (speed, heading, acceleration, lateral)
        if any(value is not None and not is_finite_number(value) for value in values):
            raise PipelineError("VL-206", f"Non-finite or non-numeric metric in frame {frame_id}")
        if index == 0:
            valid = all(value is None for value in values)
        else:
            valid = speed is not None and speed >= 0
            valid = valid and ((acceleration is None) if index == 1 else (acceleration is not None))
            # A heading is undefined only for a stationary/nearly stationary step.
            dt = timestamp - rows[index - 1][2]
            if speed is not None:
                valid = valid and ((heading is None) == (speed * dt < 1e-5))
            needs_lateral = previous_heading is not None and heading is not None
            valid = valid and ((lateral is not None) == needs_lateral)
        if not valid:
            raise PipelineError("VL-206", f"Missing or inconsistent metric in frame {frame_id}")
        previous_heading = heading


def calculate_kinematics(conn: sqlite3.Connection, trip_id: int) -> list[tuple]:
    """ Compute frame metrics from validated positions without writing to the DB """

    rows = load_validated_positions(conn, trip_id)
    processor = MotionProcessor()
    metric_rows = []
    for frame_id, timestamp, x, y, z in rows:
        position = np.array([x, y, z], dtype=np.float64)
        metrics = processor.process_position(position, timestamp)
        metric_rows.append((
            trip_id, frame_id, timestamp, metrics["speed"], metrics["heading"],
            metrics["acceleration"], metrics["lateral_acceleration"],
        ))
    validate_metrics(metric_rows)
    return metric_rows


def detect_driving_events(metric_rows: list[tuple], trip_id: int) -> list[tuple]:
    """ Detect events from time-ordered motion metrics """
    
    if not metric_rows:
        raise PipelineError("VL-203", f"No motion metrics for trip {trip_id}")

    detector = EventDetector()

    for _, _, timestamp, speed, heading, acceleration, lateral_acceleration in metric_rows:
        metrics = {
            "timestamp": timestamp,
            "speed": speed,
            "heading": heading,
            "acceleration": acceleration,
            "lateral_acceleration": lateral_acceleration,
        }
        detector.process_frame(metrics)

    detector.finalize()
    event_rows = [
        (trip_id, e["type"], e["start_time"], e["end_time"], e["peak_value"])
        for e in detector.events
    ]
    return event_rows


def load_stored_metrics(conn: sqlite3.Connection, trip_id: int) -> list[tuple]:
    """ Read previously calculated metrics, do not recompute kinematics """

    positions = load_validated_positions(conn, trip_id)
    rows = conn.execute(
        """SELECT m.trip_id, m.frame_id, p.elapsed_sec, m.speed_mps,
                  m.heading, m.acceleration_mps2, m.lateral_acceleration_mps2
           FROM frame_metrics AS m
           JOIN frame_positions AS p
             ON m.trip_id = p.trip_id AND m.frame_id = p.frame_id
           WHERE m.trip_id = ? ORDER BY m.frame_id""",
        (trip_id,),
    ).fetchall()
    expected = len(positions)
    if not expected or len(rows) != expected:
        raise PipelineError(
            "VL-203",
            f"Trip {trip_id}: expected {expected} metrics, found {len(rows)}. "
            "Run full analysis first."
        )
    validate_metrics(rows)
    return rows


def mark_trip_failed(conn: sqlite3.Connection, trip_id: int, code: str) -> None:
    """ Persist a failure for an existing trip after prior writes are rolled back """
    
    conn.rollback()
    with conn:
        conn.execute(
            "UPDATE trips SET status = 'failed', error_code = ? WHERE trip_id = ?",
            (code, trip_id),
        )


def analyze_trip(conn: sqlite3.Connection, trip_id: int, events_only: bool = False) -> dict:
    """ Run full analysis, or detect events from previously stored frame metrics """
    
    if events_only:
        metric_rows = load_stored_metrics(conn, trip_id)
    else:
        metric_rows = calculate_kinematics(conn, trip_id)

    event_rows = detect_driving_events(metric_rows, trip_id)

    with conn:
        if not events_only:
            conn.execute("DELETE FROM frame_metrics WHERE trip_id = ?", (trip_id,))
            conn.executemany(
                """INSERT INTO frame_metrics
                   (trip_id, frame_id, speed_mps, heading,
                    acceleration_mps2, lateral_acceleration_mps2)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (trip, frame, speed, heading, acceleration, lateral)
                    for trip, frame, _, speed, heading, acceleration, lateral in metric_rows
                ],
            )

        conn.execute("DELETE FROM driving_events WHERE trip_id = ?", (trip_id,))
        conn.executemany(
            """INSERT INTO driving_events
               (trip_id, event_type, start_time_sec, end_time_sec, peak_value)
               VALUES (?, ?, ?, ?, ?)""",
            event_rows,
        )
        conn.execute(
            "UPDATE trips SET status = 'analyzed', error_code = NULL WHERE trip_id = ?",
            (trip_id,),
        )
    # Use the same SQL summary and scores as the dashboard
    cursor = conn.execute(
        "SELECT * FROM trip_summary WHERE trip_id = ?", (trip_id,)
    )
    return dict(zip((column[0] for column in cursor.description), cursor.fetchone()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a trip from SQLite")
    parser.add_argument("--trip", type=int, required=True, help="Trip ID")
    parser.add_argument(
        "--events-only", action="store_true",
        help="Recalculate events without changing frame_metrics",
    )
    args = parser.parse_args()

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        create_tables(conn)
        create_views(conn)
        summary = analyze_trip(conn, args.trip, events_only=args.events_only)
        print(f"Trip {args.trip} analyzed successfully")
        for key, value in summary.items():
            print(f"{key}: {value}")
        count = conn.execute(
            "SELECT COUNT(*) FROM driving_events WHERE trip_id = ?", (args.trip,)
        ).fetchone()[0]
        print(f"Detected events: {count}")
    except PipelineError as error:
        print(error, file=sys.stderr)
        if conn is not None:
            try:
                mark_trip_failed(conn, args.trip, error.code)
            except sqlite3.Error as db_error:
                print(f"[VL-301] Could not record failure: {db_error}", file=sys.stderr)
        raise SystemExit(1)
    except sqlite3.Error as error:
        print(f"[VL-301] Database operation failed: {error}", file=sys.stderr)
        if conn is not None:
            try:
                mark_trip_failed(conn, args.trip, "VL-301")
            except sqlite3.Error as db_error:
                print(f"[VL-301] Could not record failure: {db_error}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
