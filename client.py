
import asyncio
import numpy as np
import json
import time
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedOK

async def receive_clouds():
    async with connect(
        "ws://127.0.0.1:8000/cloud?scenario=aggressive",
        max_size=None,
        compression=None,
        ping_interval=60,
        ping_timeout=60
    ) as websocket:

        start_time = time.perf_counter()
        frame_count = 0

        try:
            while True:
                metadata = await websocket.recv()
                data = await websocket.recv()

                metadata = json.loads(metadata)

                cloud = np.frombuffer(
                    data,
                    dtype=np.dtype(metadata["dtype"])
                    ).reshape(metadata["shape"])
                frame_count += 1

                # Print only occasionally
                if metadata["frame_id"] % 100 == 0:
                    print(
                        metadata["frame_id"],
                        metadata["timestamp"],
                        cloud[0]
                    )

        except ConnectionClosedOK:
            elapsed = time.perf_counter() - start_time
            actual_fps = frame_count / elapsed

            print(f"Frames received: {frame_count}")
            print(f"Elapsed time: {elapsed:.2f} seconds")
            print(f"Average FPS: {actual_fps:.2f}")
            print("Stream completed successfully")

if __name__ == "__main__":
    asyncio.run(receive_clouds())
