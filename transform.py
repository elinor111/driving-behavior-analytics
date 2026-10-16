import numpy as np

# Driving score configuration
MIN_SCORING_DISTANCE_M = 500
REFERENCE_EVENTS_PER_KM = 5.0


def extract_position(cloud: np.ndarray) -> np.ndarray:
    """
    Estimate vehicle position as the point cloud centroid.
    Input: 3D coordinates in meters. Returns [x, y, z] in meters.
    Assumes a complete, rigid point cloud with stable geometry.
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


def calculate_driving_scores(rapid_count: int, braking_count: int, cornering_count: int, distance_m: float) -> dict:
    """
    Calculate three behavior scores and their overall average.
    Event counts are normalized by distance (km), scores range from 0 to 100.
    Returns None scores for trips shorter than the minimum scoring distance.
    """

    if rapid_count < 0 or braking_count < 0 or cornering_count < 0:
        raise ValueError("Event counts cannot be negative")
    if distance_m < 0:
        raise ValueError("Distance cannot be negative")

    if distance_m < MIN_SCORING_DISTANCE_M:
        return {
            "acceleration_score": None,
            "braking_score": None,
            "cornering_score": None,
            "overall_score": None}

    distance_km = distance_m / 1000

    #  Calculate event rate per km
    r_acc = rapid_count / distance_km
    r_brake = braking_count / distance_km
    r_corner = cornering_count / distance_km

    # Convert event rates to scores between 0 and 100
    s_acc = 100 * max(0, 1 - r_acc / REFERENCE_EVENTS_PER_KM)
    s_brake = 100 * max(0, 1 - r_brake / REFERENCE_EVENTS_PER_KM)
    s_corner = 100 * max(0, 1 - r_corner / REFERENCE_EVENTS_PER_KM)

    s_overall = (s_acc + s_brake + s_corner) / 3

    return {
        "acceleration_score": s_acc,
        "braking_score": s_brake,
        "cornering_score": s_corner,
        "overall_score": s_overall}


class MotionProcessor:
    def __init__(self):
        self.previous_position = None
        self.previous_timestamp = None
        self.previous_speed = None
        self.previous_heading = None

    def process_frame(self, cloud: np.ndarray, timestamp: float) -> dict:
        position = extract_position(cloud)

        speed = None
        acceleration = None
        heading = None
        lateral_acceleration = None

        if self.previous_position is not None:
            dt = timestamp - self.previous_timestamp
            speed = calculate_speed(self.previous_position, position, self.previous_timestamp, timestamp)
            heading = calculate_heading(self.previous_position, position)

        if self.previous_speed is not None and speed is not None:
            acceleration = calculate_acceleration(self.previous_speed, speed, dt)

        if self.previous_heading is not None and heading is not None:
            lateral_acceleration = calculate_lateral_acceleration(self.previous_heading, heading, speed, dt)

        # save current state for the next frame
        self.previous_position = position
        self.previous_timestamp = timestamp
        self.previous_speed = speed
        self.previous_heading = heading

        return {
        "timestamp": timestamp,
        "x": float(position[0]),
        "y": float(position[1]),
        "speed": speed,
        "acceleration": acceleration,
        "heading": heading,
        "lateral_acceleration": lateral_acceleration}


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


class TripAnalyzer():
    """ Accumulates trip metrics and generate a summary """

    def __init__(self, trip_id: str):
        self.trip_id = trip_id

        self.start_time = None
        self.end_time = None
        self.total_distance = 0.0
        self.max_speed = 0.0

    def process_frame(self, metrics: dict):
        """
        Update trip statistics using motion metrics from the current frame.
        Accumulates distance (speed * dt, in meters) and tracks maximum speed (m/s).
        Initializes the trip start time and requires strictly increasing timestamps.
        """
        
        speed = metrics["speed"]
        timestamp = metrics["timestamp"]

        # First frame: initialize trip start time
        if self.start_time is None:
            self.start_time = timestamp

        # Calculate distance only when previous timestamp and speed exist
        if self.end_time is not None and speed is not None:
            dt = timestamp - self.end_time

            if dt <= 0:
                raise ValueError("Timestamps must be strictly increasing")

            self.total_distance += speed * dt
            self.max_speed = max(self.max_speed, speed)
            
        # Update the timestamp of the latest processed frame
        self.end_time = timestamp

    def get_summary(self, events: list[dict]) -> dict:
        """ Return trip duration, distance and speed statistics (including events and scores) """

        if self.start_time is None or self.end_time is None:
            raise ValueError("No frames were processed")

        duration = self.end_time - self.start_time

        # convert from m/s to km/h
        max_speed = self.max_speed * 3.6

        if duration > 0:
            avg_speed = (self.total_distance / duration) * 3.6
        else:
            avg_speed = 0

        rapid_count = 0
        braking_count = 0
        cornering_count = 0

        for event in events:
            if event["type"] == "rapid_acceleration":
                rapid_count += 1

            elif event["type"] == "harsh_braking":
                braking_count += 1

            elif event["type"] == "hard_cornering":
                cornering_count += 1

        scores = calculate_driving_scores(rapid_count, braking_count, cornering_count, self.total_distance)

        return {
            "trip_id": self.trip_id,
            "duration_sec": duration,
            "distance_m": self.total_distance,
            "avg_speed_kmh": avg_speed,
            "max_speed_kmh": max_speed, 
            "rapid_acceleration_count": rapid_count,
            "harsh_braking_count": braking_count,
            "hard_cornering_count": cornering_count,
            **scores
        }


