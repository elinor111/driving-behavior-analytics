import numpy as np


def extract_position(cloud: np.ndarray) -> np.ndarray:
    """
    Estimate vehicle position as the point cloud centroid.
    Input: 3D coordinates in meters. Returns [x, y, z] in meters.
    Assumes a complete, rigid point cloud.
    """
    return cloud.mean(axis=0, dtype=np.float64)


def calculate_speed(p_position: np.ndarray, c_position:np.ndarray, time_p: float, time_c: float) -> float:
    """
    Compute average speed = XY displacement / elapsed time.
    Positions are in meters, timestamps in seconds: returns m/s.
    Assumes planar motion and strictly increasing timestamps.
    """

    dt = time_c - time_p
    if dt <= 0:
        raise ValueError("Timestamps must be strictly increasing")

    difference = c_position[:2] - p_position[:2]
    d = np.linalg.norm(difference)

    return float(d / dt) 

 
def calculate_acceleration(speed_1: float, speed_2: float, dt: float) -> float:
    """
    Estimate longitudinal acceleration = change in speed / dt.
    Speeds are in m/s, dt in seconds: returns m/s².
    Negative results indicate deceleration.
    """

    if dt <= 0:
        raise ValueError("Time difference must be positive")
    if speed_1 < 0 or speed_2 < 0:
        raise ValueError("Speed cannot be negative") 
    
    d_speed = speed_2 - speed_1

    return d_speed / dt 


def calculate_heading(p_position: np.ndarray, c_position: np.ndarray) -> float | None:
    """
    Compute motion direction using atan2(dy, dx).
    Positions are in meters, returns radians relative to the +X axis.
    Returns None when XY displacement is below 1e-5 meters, as the vehicle is stationary or nearly stationary,
    making its direction of motion unreliable to estimate.
    """

    dx = c_position[0] - p_position[0]
    dy = c_position[1] - p_position[1]

    if np.hypot(dx, dy) < 1e-5:
        return None

    return float(np.arctan2(dy, dx))


def calculate_lateral_acceleration(p_heading: float | None, c_heading: float | None, speed: float, dt: float) -> float | None:
    """
    Estimate signed lateral acceleration = speed * angular rate.
    Headings are in radians, speed in m/s, dt in seconds: returns m/s².
    Wraps angle differences to [-pi, pi), returns None for undefined headings.
    """

    if dt <= 0:
            raise ValueError("Time difference must be positive")
    if speed < 0:
        raise ValueError("Speed cannot be negative") 
    if p_heading is None or c_heading is None: 
        return None

    d_heading = c_heading - p_heading
    d_heading = (d_heading + np.pi) % (2 * np.pi) - np.pi

    return float(speed * d_heading / dt)



class MotionProcessor:
    """ Calculate motion metrics from consecutive vehicle positions """

    def __init__(self):
        self.previous_position = None
        self.previous_timestamp = None
        self.previous_speed = None
        self.previous_heading = None


    def process_position(self, position: np.ndarray, timestamp: float) -> dict:
        """ Calculate speed, acceleration, and direction from the current position and previous motion data """

        speed = None
        acceleration = None
        heading = None
        lateral_acceleration = None

        if self.previous_position is not None:
            dt = timestamp - self.previous_timestamp

            speed = calculate_speed(self.previous_position, position, self.previous_timestamp, timestamp)

            heading = calculate_heading(self.previous_position, position)

            if self.previous_speed is not None:
                acceleration = calculate_acceleration(self.previous_speed, speed, dt)

            if self.previous_heading is not None:
                lateral_acceleration = calculate_lateral_acceleration(self.previous_heading, heading, speed, dt)

        self.previous_position = position.copy()
        self.previous_timestamp = timestamp
        self.previous_speed = speed
        self.previous_heading = heading

        return {
            "timestamp": timestamp,
            "speed": speed,
            "acceleration": acceleration,
            "heading": heading,
            "lateral_acceleration": lateral_acceleration
        }


class EventDetector:
    """ Detect consecutive frames exceeding driving-behavior thresholds """

    THRESHOLDS = {
        "rapid_acceleration": 3.0,
        "harsh_braking": -3.0,
        "hard_cornering": 3.0
    }

    END_THRESHOLDS = {
    "rapid_acceleration": 2.95,
    "harsh_braking": -2.95,
    "hard_cornering": 2.95
    }

    EVENT_METRICS = {
    "rapid_acceleration": "acceleration",
    "harsh_braking": "acceleration",
    "hard_cornering": "lateral_acceleration"
    }

    def __init__(self):
        # Active events, indexed by event type
        self.active_events = {}

        # Completed events
        self.events = []

    def detect_conditions(self, metrics: dict) -> dict:
        """
        Check which driving events are occurring in the current frame.
        Uses different thresholds to start and end an event,
        preventing small measurement fluctuations from splitting
        one continuous event into multiple events.
        """

        acceleration = metrics["acceleration"]
        lateral_acceleration = metrics["lateral_acceleration"]

        event_dict = {"rapid_acceleration": False, "harsh_braking": False, "hard_cornering": False}

        if acceleration is not None:

            # rapid acceleration
            if "rapid_acceleration" in self.active_events:
                event_dict["rapid_acceleration"] = acceleration >= self.END_THRESHOLDS["rapid_acceleration"]
            else:
                event_dict["rapid_acceleration"] = acceleration > self.THRESHOLDS["rapid_acceleration"]

            # harsh braking 
            if "harsh_braking" in self.active_events:
                event_dict["harsh_braking"] = acceleration <= self.END_THRESHOLDS["harsh_braking"]
            else:
                event_dict["harsh_braking"] = acceleration < self.THRESHOLDS["harsh_braking"]

        if lateral_acceleration is not None:
            if "hard_cornering" in self.active_events:
                event_dict["hard_cornering"] = abs(lateral_acceleration) >= self.END_THRESHOLDS["hard_cornering"]
            else:
                event_dict["hard_cornering"] = abs(lateral_acceleration) > self.THRESHOLDS["hard_cornering"]

        return event_dict
        

    def process_frame(self, metrics: dict):
        """ Start, continue, or finish events based on current metrics """

        timestamp = metrics["timestamp"]
        conditions = self.detect_conditions(metrics)

        for event_type, is_active in conditions.items():
            metric_name = self.EVENT_METRICS[event_type]
            current_value = metrics[metric_name]

            if is_active:
                if event_type not in self.active_events:
                    self.active_events[event_type] = {
                        "type": event_type,
                        "start_time": timestamp,
                        "end_time": timestamp, 
                        "peak_value": current_value
                    }
                else:
                    self.active_events[event_type]["end_time"] = timestamp
                    
                    prev_peak = self.active_events[event_type]["peak_value"]

                    if event_type == "rapid_acceleration": 
                        new_peak = max(prev_peak, current_value)
                    elif event_type == "harsh_braking":
                        new_peak = min(prev_peak, current_value)
                    else: # hard cornering
                        if abs(current_value) > abs(prev_peak):
                            new_peak = current_value
                        else:
                            new_peak = prev_peak

                    self.active_events[event_type]["peak_value"] = new_peak

            else: # event not active anymore
                if event_type in self.active_events:
                    finished_event = self.active_events.pop(event_type)
                    self.events.append(finished_event)


    def finalize(self):
        """ Finish any events still active at the end of the trip """

        self.events.extend(self.active_events.values())
        self.active_events.clear()

