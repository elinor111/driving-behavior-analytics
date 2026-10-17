""" Diagnostic client using the same validated stream reader as ingestion """

import asyncio
import sys
import time

from errors import PipelineError
from ingest import ingest_trip


async def receive_clouds():
    start_time = time.perf_counter()
    result = await ingest_trip("aggressive")
    elapsed = time.perf_counter() - start_time
    print(f"Average FPS: {result['frame_count'] / elapsed:.2f}")
    print("Stream completed successfully (frame count verified)")


if __name__ == "__main__":
    try:
        asyncio.run(receive_clouds())
    except PipelineError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)

