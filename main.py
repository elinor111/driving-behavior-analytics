from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from generator import gen_cloud
from motion import SCENARIOS

app = FastAPI()


@app.websocket("/cloud")   # define address for a websocket connection
async def stream_cloud(websocket: WebSocket):
    await websocket.accept()    # server approves oppening of the connection

    scenario = websocket.query_params.get("scenario", "calm") # calm is the default

    if scenario not in SCENARIOS:
        await websocket.close(
            code=1008, reason="Unknown Scenario"
        )
        return

    try:
        async for frame in gen_cloud(scenario):
            metadata = {
                "frame_id": frame["frame_id"],
                "timestamp": frame["timestamp"],
                "dtype": str(frame["points"].dtype),
                "shape": list(frame["points"].shape),
                "scenario": scenario
            }

            await websocket.send_json(metadata)
            await websocket.send_bytes(frame["points"].tobytes())  # change from numpy array to bytes format

        # all frames were sent successfully
        await websocket.close(code=1000)

    except WebSocketDisconnect:
        print("Client disconnected")


