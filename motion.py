import math
from dataclasses import dataclass

@dataclass
class VehicleState:
    x: float = 0.0
    y: float = 0.0
    speed: float = 0.0
    heading: float = 0.0



def update_state(state, acceleration, turn_rate, dt): 
    """
    Update the vehicle's position, speed, and heading over one time step.

    Args:
        state (VehicleState): Current vehicle state.
        acceleration (float): Constant longitudinal acceleration
            during the time step, in m/s^2.
        turn_rate (float): Rate of change of heading, in radians
            per second. Positive values indicate counterclockwise
            rotation (left turn), negative values indicate clockwise
            rotation (right turn).
        dt (float): Time step duration, in seconds.

    Returns:
        VehicleState: A new state containing the updated position,
        speed, and heading.

    Assumptions:
        - Motion is modeled in a 2D horizontal plane (X, Y).
        - Acceleration and turn rate are constant during each step.
        - Speed and heading change linearly during the step.
        - Position is approximated using average speed and heading.
        - Vehicle orientation and physical dynamics are not modeled.
        - The current implementation assumes speed remains
          non-negative; stopping behavior is not yet handled.
    """

    # Update speed
    new_speed = state.speed + acceleration * dt

    if new_speed < 0:
        # time passed until the brake 
        t_stop = - (state.speed / acceleration)
        d = (state.speed * t_stop) + (acceleration * (t_stop**2) * 0.5)
        new_speed = 0  
    else:
        # Calculate distance traveled
        d = ((state.speed+new_speed) / 2) * dt

    # Update heading
    new_heading = state.heading + turn_rate * dt
    avg_heading = (state.heading + new_heading) / 2

    new_x = state.x + d* math.cos(avg_heading)
    new_y = state.y + d * math.sin(avg_heading)

    return VehicleState(new_x, new_y, new_speed, new_heading)


def simulate_scenario(scenarios, fps=16):
    car = VehicleState(0, 0, 0, 0)
    dt = 1 / fps

    curr_time = 0.0
    trajectory = [(curr_time, car)]

    for scenario in scenarios:
        total_duration = scenario["duration"]
        num_of_states = int(total_duration * fps)
        curr_acc = scenario["acceleration"]
        curr_turn = scenario["turn_rate"]

        for _ in range(num_of_states):
            car = update_state(car, curr_acc, curr_turn, dt)
            curr_time += dt
            trajectory.append((curr_time, car))

    return trajectory

SCENARIOS = {
    "calm": [
        {"duration": 10, "acceleration": 1.0, "turn_rate": 0.0},
        {"duration": 12, "acceleration": 0.0, "turn_rate": 0.0},
        {"duration": 8, "acceleration": 0.0, "turn_rate": math.pi / 48},
        {"duration": 12, "acceleration": 0.0, "turn_rate": 0.0},
        {"duration": 10, "acceleration": -0.5, "turn_rate": 0.0},
        {"duration": 10, "acceleration": 0.0, "turn_rate": -math.pi / 60},
        {"duration": 6, "acceleration": -5/6, "turn_rate": 0.0},
    ],

    "aggressive": [
        {"duration": 5, "acceleration": 4.0, "turn_rate": 0.0},
        {"duration": 8, "acceleration": 0.0, "turn_rate": 0.0},
        {"duration": 5, "acceleration": -3.0, "turn_rate": 0.0},
        {"duration": 6, "acceleration": 2.5, "turn_rate": 0.0},
        {"duration": 4, "acceleration": 0.0, "turn_rate": math.pi / 12},
        {"duration": 10, "acceleration": 0.0, "turn_rate": 0.0},
        {"duration": 5, "acceleration": -3.0, "turn_rate": 0.0},
        {"duration": 5, "acceleration": 3.0, "turn_rate": 0.0},
        {"duration": 4, "acceleration": 0.0, "turn_rate": -math.pi / 12},
        {"duration": 6, "acceleration": 0.0, "turn_rate": 0.0},
        {"duration": 10, "acceleration": -2.0, "turn_rate": 0.0},
    ],

    "mixed": [
        {"duration": 2, "acceleration": 3.5, "turn_rate": 0.0},
        {"duration": 6, "acceleration": 5/6, "turn_rate": 0.0},
        {"duration": 10, "acceleration": 0.0, "turn_rate": 0.0},
        {"duration": 1, "acceleration": -3.5, "turn_rate": 0.0},
        {"duration": 3, "acceleration": -8.5/3, "turn_rate": 0.0},
        {"duration": 8, "acceleration": 1.5, "turn_rate": 0.0},
        {"duration": 6, "acceleration": 0.0, "turn_rate": math.pi / 24},
        {"duration": 12, "acceleration": 0.0, "turn_rate": 0.0},
        {"duration": 8, "acceleration": -0.75, "turn_rate": 0.0},
        {"duration": 6, "acceleration": 0.0, "turn_rate": -math.pi / 36},
        {"duration": 6, "acceleration": -1.0, "turn_rate": 0.0},
    ],
}



if __name__ == "__main__":
    for name, scenario in SCENARIOS.items():
        trajectory = simulate_scenario(scenario)

        print(
            name,
            "States:", len(trajectory),
            "Duration:", round(trajectory[-1][0], 2),
            "Final speed:", trajectory[-1][1].speed
        )
