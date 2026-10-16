import numpy as np
from motion import SCENARIOS, simulate_scenario


async def gen_cloud(scenario_name="calm", fps=16):
    # create the base point cloud: 1 cm spacing in meters
    grid = np.indices((100, 100, 100), dtype=np.float32)
    base_cloud = grid.reshape(3, -1).T / 100

    # generate the vehicle trajectory
    trajectory = simulate_scenario(SCENARIOS[scenario_name], fps=fps)

    for frame_id, (timestamp, state) in enumerate(trajectory):
        position = np.array([state.x, state.y, 0.0], dtype=np.float32)

        cloud = base_cloud + position

        yield {
            "frame_id": frame_id,
            "timestamp": timestamp,
            "points": cloud
        }


        