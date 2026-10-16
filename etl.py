import asyncio
import json
import time
import numpy as np
import sqlite3

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedOK

from transform import MotionProcessor, EventDetector, TripAnalyzer
from database import create_tables, save_trip


async def process_trip(scenario: str = "calm") -> dict:
    """ Process one complete trip streamed from the WebSocket API """

    url = f"ws://127.0.0.1:8000/cloud?scenario={scenario}"

    motion_processor = MotionProcessor()
    event_detector = EventDetector()
    trip_analyzer = TripAnalyzer(trip_id=f"{scenario}_trip")

    frame_count = 0
    frame_metrics = []

    start = time.perf_counter()

    async with connect(
        url,
        max_size=None,
        compression=None,
        ping_interval=60,
        ping_timeout=60,
    ) as websocket:

        while True:
            try:
                metadata_message = await websocket.recv()

            except ConnectionClosedOK:
                # Normal end of stream, between complete frames
                break

            metadata = json.loads(metadata_message)
            # Once metadata is received, the binary payload must follow
            binary = await websocket.recv()
            

            cloud = np.frombuffer(binary, dtype=np.dtype(metadata["dtype"])).reshape(metadata["shape"])

            # 1. Extract motion metrics and store them with the frame metadata
            metrics = motion_processor.process_frame(cloud, metadata["timestamp"])
            frame_metrics.append({"frame_id": metadata["frame_id"], **metrics})

            # 2. Detect driving events
            event_detector.process_frame(metrics)

            # 3. Accumulate trip statistics
            trip_analyzer.process_frame(metrics)

            frame_count += 1

            if frame_count % 100 == 0:
                print(f"Processed {frame_count} frames...")

    # Close any events still active at the end of the trip
    event_detector.finalize()

    # Generate summary using the detected events
    summary = trip_analyzer.get_summary(event_detector.events)

    elapsed = time.perf_counter() - start

    print(f"\nProcessed {frame_count} frames in {elapsed:.2f} seconds")
    print(f"Average processing rate: {frame_count / elapsed:.2f} FPS")

    return {
        "summary": summary,
        "events": event_detector.events,
        "frame_metrics": frame_metrics,
        "frame_count": frame_count
    }


async def main():
    scenario = "aggressive"
    result = await process_trip(scenario)

    conn = sqlite3.connect("cloud.db")

    try:
        create_tables(conn)

        # Save the complete trip as one transaction.
        # Any insertion failure rolls back all trip data.
        with conn:
            trip_id = save_trip(
                conn,
                result,
                driver_id=1,
                scenario=scenario
            )

        print(f"Trip {trip_id} saved successfully!")

        trip_count = conn.execute(
            "SELECT COUNT(*) FROM trips WHERE trip_id = ?",
            (trip_id,)
        ).fetchone()[0]

        frame_count = conn.execute(
            "SELECT COUNT(*) FROM frame_metrics WHERE trip_id = ?",
            (trip_id,)
        ).fetchone()[0]

        event_count = conn.execute(
            "SELECT COUNT(*) FROM driving_events WHERE trip_id = ?",
            (trip_id,)
        ).fetchone()[0]

        print(f"DB verification: {trip_count} trip, {frame_count} frames, {event_count} events")

        assert trip_count == 1
        assert frame_count == result["frame_count"]
        assert event_count == len(result["events"])


    finally:
        conn.close()

    print("\n--- TRIP SUMMARY ---")
    print(json.dumps(result["summary"], indent=2))  

    print("\n--- DETECTED EVENTS ---")
    print(f"Total events: {len(result['events'])}")

    for event in result["events"][:10]:
        print(event)


if __name__ == "__main__":
    asyncio.run(main())
