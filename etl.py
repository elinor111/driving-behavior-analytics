
import asyncio
import json
import sqlite3
import time
import argparse

import numpy as np
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedOK

from transform import extract_position
from database import create_tables, save_positions

DB_PATH = "cloud.db"

async def ingest_trip(scenario: str = "calm") -> dict:
    """ Receive point clouds and extract frame positions """

    url = f"ws://127.0.0.1:8000/cloud?scenario={scenario}"

    positions = []
    start = time.perf_counter()

    async with connect(
        url,
        max_size=None,
        compression=None,
        ping_interval=60,
        ping_timeout=60
    ) as websocket:

        while True:
            try:
                metadata_message = await websocket.recv()
            except ConnectionClosedOK:
                break

            metadata = json.loads(metadata_message)
            binary = await websocket.recv()

            cloud = np.frombuffer(
                binary,
                dtype=np.dtype(metadata["dtype"])
            ).reshape(metadata["shape"])

            position = extract_position(cloud)

            positions.append({
                "frame_id": metadata["frame_id"],
                "timestamp": metadata["timestamp"],
                "x": float(position[0]),
                "y": float(position[1]),
                "z": float(position[2])
            })

            if len(positions) % 100 == 0:
                print(f"Received {len(positions)} frames...")

    elapsed = time.perf_counter() - start

    print(f"\nReceived {len(positions)} frames in {elapsed:.2f} seconds")

    if not positions:
        raise ValueError("No frames received")

    return {
        "positions": positions,
        "frame_count": len(positions)
    }


async def main():
    parser = argparse.ArgumentParser(
        description="Ingest simulated driving trips into SQLite")

    parser.add_argument(
        "--scenario",
        choices=["calm", "mixed", "aggressive"],
        default="calm",
        help="Driving scenario to simulate"
    )

    parser.add_argument(
        "--driver",
        type=int,
        default=1,
        help="Driver ID"
    )

    args = parser.parse_args()

    if args.driver <= 0:
        parser.error("--driver must be a positive integer")

    scenario = args.scenario
    driver_id = args.driver

    result = await ingest_trip(scenario)

    conn = sqlite3.connect(DB_PATH)

    try:
        create_tables(conn)

        with conn:
            trip_id = save_positions(
                conn,
                driver_id=driver_id,
                positions=result["positions"],
                source_scenario=scenario
            )

        saved_frames = conn.execute(
            """
            SELECT COUNT(*)
            FROM frame_positions
            WHERE trip_id = ?
            """,
            (trip_id,)
        ).fetchone()[0]

        assert saved_frames == result["frame_count"]

        print(f"Trip {trip_id} saved successfully")
        print(f"Verified {saved_frames} stored positions")

    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
