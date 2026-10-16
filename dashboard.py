import sqlite3 
import streamlit as st
import pandas as pd
from pathlib import Path 

DB_PATH = Path(__file__).with_name("cloud.db")


def load_trips() -> pd.DataFrame:
    """ Load saved trip summaries from SQLite """

    conn = sqlite3.connect(DB_PATH)

    try:
        trips = pd.read_sql_query(
            """
            SELECT *
            FROM trips
            ORDER BY recorded_at DESC, trip_id DESC
            """,
            conn
        )
        return trips

    finally:
        conn.close()


def load_frame_metrics(trip_id: int) -> pd.DataFrame:
    """ Load time-ordered motion metrics for a selected trip """

    conn = sqlite3.connect(DB_PATH)

    try:
        frames = pd.read_sql_query(
            """
            SELECT
                frame_id,
                elapsed_sec,
                speed_mps,
                acceleration_mps2,
                lateral_acceleration_mps2
            FROM frame_metrics
            WHERE trip_id = ?
            ORDER BY frame_id
            """,
            conn,
            params=(trip_id,)
        )

        return frames

    finally:
        conn.close()

def load_driving_events(trip_id: int) -> pd.DataFrame:
    """ Load detected driving events for the selected trip """

    conn = sqlite3.connect(DB_PATH)

    try:
        events = pd.read_sql_query(
            """
            SELECT
                event_type,
                start_time_sec,
                end_time_sec,
                peak_value
            FROM driving_events
            WHERE trip_id = ?
            ORDER BY start_time_sec
            """,
            conn,
            params=(trip_id,)
        )
        return events

    finally:
        conn.close()


def main():
    st.set_page_config(
        page_title="Driving Behavior Analytics",
        page_icon="🚙",
        layout="wide"
    )

    st.title("Driving Behavior Analytics")
    st.caption("Trip-level driving insights from processed vehicle location data")

    trips = load_trips()

    if trips.empty:  
        st.info("No trips found. Run the ETL to save a trip first.")
        st.stop()

    # Select a driver
    driver_ids = sorted(trips["driver_id"].unique().tolist())

    selected_driver = st.selectbox(
        "Select driver",
        driver_ids
    )

    # Show only trips belonging to the selected driver
    driver_trips = trips[trips["driver_id"] == selected_driver]

    trip_ids = driver_trips["trip_id"].tolist()

    selected_trip_id = st.selectbox(
        "Select trip",
        trip_ids,
        format_func=lambda trip_id: f"Trip #{trip_id}"
    )

    # Find the selected trip
    selected_trip = driver_trips[
        driver_trips["trip_id"] == selected_trip_id
    ].iloc[0]

    st.subheader("Trip Overview")

    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        "Driving Score",
        f"{selected_trip['overall_score']:.1f}/100"
        if pd.notna(selected_trip["overall_score"]) else "N/A"
    )

    col2.metric(
        "Distance",
        f"{selected_trip['distance_m'] / 1000:.2f} km"
    )

    col3.metric(
        "Average Speed",
        f"{selected_trip['avg_speed_kmh']:.1f} km/h"
    )

    col4.metric(
        "Max Speed",
        f"{selected_trip['max_speed_kmh']:.1f} km/h"
    )

    st.subheader("Driving Behavior Scores")

    score_columns = st.columns(3)

    score_fields = [
        ("Acceleration", "acceleration_score"),
        ("Braking", "braking_score"),
        ("Cornering", "cornering_score")
    ]

    for col, (label, field) in zip(score_columns, score_fields):
        score = selected_trip[field]

        with col:
            st.metric(
                label,
                f"{score:.1f}/100" if pd.notna(score) else "N/A"
            )

            if pd.notna(score):
                st.progress(float(score) / 100)


    st.subheader("Speed Profile")

    frames = load_frame_metrics(int(selected_trip_id))

    if frames.empty:
        st.warning("No frame metrics found for this trip")

    else:
        # Convert speed from m/s to km/h for display
        frames["speed_kmh"] = frames["speed_mps"] * 3.6

        st.line_chart(
            frames,
            x="elapsed_sec",
            y="speed_kmh",
            x_label="Time (seconds)",
            y_label="Speed (km/h)"
        )

        st.subheader("Acceleration Analysis")

        acc_col, lateral_col = st.columns(2)

        with acc_col:
            st.markdown("**Longitudinal Acceleration**")
            st.line_chart(
                frames,
                x="elapsed_sec",
                y="acceleration_mps2",
                x_label="Time (seconds)",
                y_label="Acceleration (m/s²)"
            )

        with lateral_col:
            st.markdown("**Lateral Acceleration**")
            st.line_chart(
                frames,
                x="elapsed_sec",
                y="lateral_acceleration_mps2",
                x_label="Time (seconds)",
                y_label="Lateral acceleration (m/s²)"
            )

    st.subheader("Detected Driving Events")

    events = load_driving_events(int(selected_trip_id))

    event_col1, event_col2, event_col3 = st.columns(3)

    event_col1.metric(
        "Rapid Accelerations",
        int(selected_trip["rapid_acceleration_count"])
    )

    event_col2.metric(
        "Harsh Braking",
        int(selected_trip["harsh_braking_count"])
    )

    event_col3.metric(
        "Hard Cornering",
        int(selected_trip["hard_cornering_count"])
    )

    if events.empty:
        st.success("No threshold exceeding driving events detected")

    else:
        display_events = events.rename(columns={
            "event_type": "Event Type",
            "start_time_sec": "Start (s)",
            "end_time_sec": "End (s)",
            "peak_value": "Peak Acceleration (m/s²)"
        })

        st.dataframe(
            display_events,
            use_container_width=True,
            hide_index=True
        )

if __name__ == "__main__":
    main()
