""" Ingest vehicle-location point clouds from WebSocket into SQLite """

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from database import create_tables, save_positions
from errors import PipelineError
from transform import extract_position

DB_PATH = Path(__file__).with_name("cloud.db")


def decode_frame(metadata_message, binary, previous_frame_id, previous_timestamp):
    """ Validate a metadata/binary pair and return its extracted position """
    
    try:
        if not isinstance(metadata_message, str) or not isinstance(binary, bytes):
            raise ValueError("Expected JSON text followed by binary data")
        metadata = json.loads(metadata_message)
        if not isinstance(metadata, dict):
            raise ValueError("Metadata must be a JSON object")

        frame_id = metadata["frame_id"]
        timestamp = metadata["timestamp"]
        shape = metadata["shape"]
        dtype = np.dtype(metadata["dtype"])

        if not isinstance(frame_id, int) or isinstance(frame_id, bool) or frame_id < 0:
            raise ValueError("Invalid frame_id")
        expected_frame_id = 0 if previous_frame_id is None else previous_frame_id + 1
        if frame_id != expected_frame_id:
            raise ValueError("Expected consecutive frame IDs starting at zero")
        if (not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool)
                or not np.isfinite(timestamp) or timestamp < 0):
            raise ValueError("Invalid timestamp")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("Timestamps must increase")
        if (not isinstance(shape, list) or len(shape) != 2
                or not all(type(n) is int and n > 0 for n in shape)
                or shape[1] != 3 or shape[0] > 10_000_000):
            raise ValueError("Expected a point cloud with shape [N, 3]")
        if dtype not in (np.dtype("float32"), np.dtype("float64")):
            raise ValueError("Expected float32 or float64 point coordinates")
        if len(binary) != shape[0] * shape[1] * dtype.itemsize:
            raise ValueError("Payload byte size does not match metadata")

        cloud = np.frombuffer(binary, dtype=dtype).reshape(shape)
        position = extract_position(cloud)
        if not np.isfinite(position).all():
            raise ValueError("Non-finite extracted position")
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise PipelineError("VL-103", f"Invalid frame payload: {exc}") from exc

    return {
        "frame_id": frame_id,
        "timestamp": timestamp,
        "x": float(position[0]),
        "y": float(position[1]),
        "z": float(position[2]),
    }


async def ingest_trip(scenario: str = "calm") -> dict:
    """ Receive full frames and extract positions, doesn't save a partial stream """

    url = f"ws://127.0.0.1:8000/cloud?scenario={scenario}"
    positions = []
    start = time.perf_counter()

    try:
        async with connect(
            url, max_size=None, compression=None,
            ping_interval=60, ping_timeout=60,
        ) as websocket:
            while True:
                try:
                    metadata_message = await websocket.recv()
                except ConnectionClosed as exc:
                    raise PipelineError("VL-102", "Stream closed before the end-of-trip message") from exc

                try:
                    metadata = json.loads(metadata_message) if isinstance(metadata_message, str) else None
                    if not isinstance(metadata, dict):
                        raise ValueError("Expected a JSON metadata object")
                except (ValueError, TypeError) as exc:
                    raise PipelineError("VL-103", f"Invalid metadata: {exc}") from exc

                if metadata.get("type") == "end":
                    expected_count = metadata.get("frame_count")
                    if type(expected_count) is not int or expected_count < 0:
                        raise PipelineError("VL-103", "Invalid end-of-trip frame_count")
                    if expected_count != len(positions):
                        raise PipelineError(
                            "VL-102",
                            f"Incomplete trip: expected {expected_count} frames, received {len(positions)}",
                        )
                    break  # Explicit, verified completion; no binary payload follows.

                try:
                    binary = await websocket.recv()
                except ConnectionClosed as exc:
                    raise PipelineError("VL-102", "Stream ended mid-frame") from exc

                previous = positions[-1] if positions else None
                position = decode_frame(
                    metadata_message, binary,
                    previous["frame_id"] if previous else None,
                    previous["timestamp"] if previous else None,
                )
                positions.append(position)
                if len(positions) % 100 == 0:
                    print(f"Received {len(positions)} frames...")
    except (OSError, TimeoutError, InvalidHandshake) as exc:
        if positions:
            raise PipelineError("VL-102", f"Stream interrupted: {exc}") from exc
        raise PipelineError("VL-101", f"Source unavailable: {exc}") from exc

    elapsed = time.perf_counter() - start
    print(f"\nReceived {len(positions)} frames in {elapsed:.2f} seconds")
    if not positions:
        raise PipelineError("VL-104", "No frames received")
    return {"positions": positions, "frame_count": len(positions)}


async def main():
    parser = argparse.ArgumentParser(description="Ingest simulated driving trips into SQLite")
    parser.add_argument("--scenario", choices=["calm", "mixed", "aggressive"], default="calm")
    parser.add_argument("--driver", type=int, default=1, help="Positive driver ID")
    args = parser.parse_args()
    if args.driver <= 0:
        parser.error("--driver must be a positive integer")

    result = await ingest_trip(args.scenario)
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        create_tables(conn)
        with conn:
            trip_id = save_positions(
                conn, driver_id=args.driver,
                positions=result["positions"], source_scenario=args.scenario,
            )
            saved_frames = conn.execute(
                "SELECT COUNT(*) FROM frame_positions WHERE trip_id = ?", (trip_id,)
            ).fetchone()[0]
            if saved_frames != result["frame_count"]:
                raise sqlite3.DatabaseError(
                    f"Stored {saved_frames} of {result['frame_count']} frames"
                )
    except sqlite3.Error as exc:
        raise PipelineError("VL-301", f"Database operation failed: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()

    print(f"Trip {trip_id} saved successfully")
    print(f"Verified {saved_frames} stored positions")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except PipelineError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
