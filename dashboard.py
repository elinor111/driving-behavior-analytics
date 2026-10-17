import sqlite3 
import streamlit as st
import pandas as pd
import altair as alt
from pathlib import Path

from transform import EventDetector

DB_PATH = Path(__file__).with_name("cloud.db")


def load_trips() -> pd.DataFrame:
    """Load summaries, or return an empty result before the first analysis."""

    if not DB_PATH.is_file():
        return pd.DataFrame()

    conn = sqlite3.connect(DB_PATH)

    try:
        summary_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = 'trip_summary'"
        ).fetchone()
        if summary_exists is None:
            return pd.DataFrame()

        trips = pd.read_sql_query(
            """
            SELECT *
            FROM trip_summary
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
                p.frame_id,
                p.elapsed_sec,
                p.x,
                p.y,
                m.speed_mps,
                m.acceleration_mps2,
                m.lateral_acceleration_mps2
            FROM frame_positions AS p
            JOIN frame_metrics AS m
              ON m.trip_id = p.trip_id
             AND m.frame_id = p.frame_id
            WHERE p.trip_id = ?
            ORDER BY p.frame_id
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


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

PRIMARY = "#0B3D91"  # dark blue, matches .streamlit/config.toml

EVENT_LABELS = {
    "rapid_acceleration": "Rapid acceleration",
    "harsh_braking": "Harsh braking",
    "hard_cornering": "Hard cornering",
}

# One fixed colour per event type, used in every chart
EVENT_COLORS = alt.Scale(
    domain=list(EVENT_LABELS.values()),
    range=["#E08E0B", "#C0392B", "#7D3C98"],
)


def format_score(value) -> str:
    return f"{value:.0f}/100" if pd.notna(value) else "N/A"


def time_chart(frames, events, column, title, threshold=None):
    """Line chart over time with shaded event intervals and optional threshold lines."""

    line = alt.Chart(frames).mark_line(color=PRIMARY, strokeWidth=2).encode(
        x=alt.X("elapsed_sec:Q", title="Time (s)"),
        y=alt.Y(f"{column}:Q", title=title),
        tooltip=[
            alt.Tooltip("elapsed_sec:Q", title="Time (s)", format=".2f"),
            alt.Tooltip(f"{column}:Q", title=title, format=".2f"),
        ],
    )
    layers = []

    if not events.empty:
        layers.append(
            alt.Chart(events).mark_rect(opacity=0.15).encode(
                x="start_time_sec:Q",
                x2="end_time_sec:Q",
                color=alt.Color("event_label:N", scale=EVENT_COLORS, title="Event"),
            )
        )

    layers.append(line)

    if threshold is not None:
        rules = pd.DataFrame({"value": [threshold, -threshold]})
        layers.append(
            alt.Chart(rules).mark_rule(color="#8A94A6", strokeDash=[4, 4]).encode(y="value:Q")
        )

    return alt.layer(*layers).properties(height=260)


def show_history(driver_trips: pd.DataFrame) -> None:
    """Compare all analyzed trips of the selected driver."""

    st.subheader("Driving history")
    st.caption("Analyzed trips of the selected driver, oldest first")

    history = driver_trips.sort_values("recorded_at").copy()
    total_events = (
        history["rapid_acceleration_count"]
        + history["harsh_braking_count"]
        + history["hard_cornering_count"]
    )
    history["events_per_km"] = total_events / (history["distance_m"] / 1000)
    history["distance_km"] = history["distance_m"] / 1000
    history["trip_label"] = (
        "#" + history["trip_id"].astype(str)
        + " " + history["source_scenario"].fillna("unknown").str.title()
    )

    col1, col2, col3 = st.columns(3)
    col1.metric("Trips", len(history), border=True)
    col2.metric("Average score", format_score(history["overall_score"].mean()), border=True)
    col3.metric("Total distance", f"{history['distance_km'].sum():.2f} km", border=True)

    with st.container(border=True):
        st.markdown("**Driving score by trip**")
        bars = alt.Chart(history).mark_bar(color=PRIMARY, cornerRadiusEnd=3).encode(
            x=alt.X("trip_label:N", title=None, sort=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("overall_score:Q", title="Score", scale=alt.Scale(domain=[0, 100])),
            tooltip=[
                alt.Tooltip("trip_label:N", title="Trip"),
                alt.Tooltip("overall_score:Q", title="Score", format=".1f"),
            ],
        ).properties(height=280)
        st.altair_chart(bars, width="stretch")

    st.dataframe(
        history[["trip_label", "distance_km", "avg_speed_kmh", "overall_score", "events_per_km"]],
        hide_index=True,
        width="stretch",
        column_config={
            "trip_label": st.column_config.TextColumn("Trip"),
            "distance_km": st.column_config.NumberColumn("Distance (km)", format="%.2f"),
            "avg_speed_kmh": st.column_config.NumberColumn("Avg speed (km/h)", format="%.1f"),
            "overall_score": st.column_config.ProgressColumn(
                "Driving score", min_value=0, max_value=100, format="%.0f"
            ),
            "events_per_km": st.column_config.NumberColumn("Events / km", format="%.1f"),
        },
    )


def show_trip(trip: pd.Series) -> None:
    """Overview, motion, trajectory and events of a single trip."""

    trip_id = int(trip["trip_id"])
    scenario = str(trip["source_scenario"]).title() if pd.notna(trip["source_scenario"]) else "Unknown"
    st.subheader(f"Trip #{trip_id}")
    st.caption(f"Scenario: {scenario} · Duration: {trip['duration_sec']:.0f} s")

    # Key numbers
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Driving score", format_score(trip["overall_score"]), border=True)
    col2.metric("Distance", f"{trip['distance_m'] / 1000:.2f} km", border=True)
    col3.metric("Average speed", f"{trip['avg_speed_kmh']:.1f} km/h", border=True)
    col4.metric("Max speed", f"{trip['max_speed_kmh']:.1f} km/h", border=True)

    # Category scores
    with st.container(border=True):
        score_columns = st.columns(3)
        score_fields = [
            ("Acceleration", "acceleration_score", "rapid_acceleration_count"),
            ("Braking", "braking_score", "harsh_braking_count"),
            ("Cornering", "cornering_score", "hard_cornering_count"),
        ]
        for col, (label, score_field, count_field) in zip(score_columns, score_fields):
            score = trip[score_field]
            with col:
                st.markdown(f"**{label}** · {format_score(score)}")
                st.progress(float(score) / 100 if pd.notna(score) else 0.0)
                st.caption(f"{int(trip[count_field])} event(s)")

    frames = load_frame_metrics(trip_id)
    events = load_driving_events(trip_id)
    events["event_label"] = events["event_type"].map(EVENT_LABELS).fillna(events["event_type"])

    if frames.empty:
        st.warning("No frame metrics found for this trip")
        return

    frames["speed_kmh"] = frames["speed_mps"] * 3.6

    motion_tab, route_tab, events_tab = st.tabs(["Motion", "Trajectory", "Events"])

    with motion_tab:
        st.caption("Shaded areas mark detected events. Dashed lines show detection thresholds.")
        st.altair_chart(
            time_chart(frames, events, "speed_kmh", "Speed (km/h)"),
            width="stretch",
        )
        acc_col, lateral_col = st.columns(2)
        with acc_col:
            st.altair_chart(
                time_chart(
                    frames, events, "acceleration_mps2", "Longitudinal acc. (m/s²)",
                    threshold=EventDetector.THRESHOLDS["rapid_acceleration"],
                ),
                width="stretch",
            )
        with lateral_col:
            st.altair_chart(
                time_chart(
                    frames, events, "lateral_acceleration_mps2", "Lateral acc. (m/s²)",
                    threshold=EventDetector.THRESHOLDS["hard_cornering"],
                ),
                width="stretch",
            )

    with route_tab:
        st.caption(
            "XY path estimated from point-cloud centroids. "
            "Markers show the closest recorded position to each event midpoint."
        )
        path = alt.Chart(frames).mark_line(color=PRIMARY, strokeWidth=2.5).encode(
            x=alt.X("x:Q", title="X (m)", scale=alt.Scale(zero=False)),
            y=alt.Y("y:Q", title="Y (m)", scale=alt.Scale(zero=False)),
            order="frame_id:Q",
            tooltip=[
                alt.Tooltip("elapsed_sec:Q", title="Time (s)", format=".2f"),
                alt.Tooltip("speed_kmh:Q", title="Speed (km/h)", format=".1f"),
            ],
        )
        chart = path

        if not events.empty:
            locations = events.copy()
            locations["event_time_sec"] = (locations["start_time_sec"] + locations["end_time_sec"]) / 2
            locations = pd.merge_asof(
                locations.sort_values("event_time_sec"),
                frames[["elapsed_sec", "x", "y"]].sort_values("elapsed_sec"),
                left_on="event_time_sec",
                right_on="elapsed_sec",
                direction="nearest",
            )
            markers = alt.Chart(locations).mark_circle(size=140, opacity=0.9).encode(
                x="x:Q",
                y="y:Q",
                color=alt.Color("event_label:N", scale=EVENT_COLORS, title="Event"),
                tooltip=[
                    alt.Tooltip("event_label:N", title="Event"),
                    alt.Tooltip("start_time_sec:Q", title="Start (s)", format=".2f"),
                    alt.Tooltip("end_time_sec:Q", title="End (s)", format=".2f"),
                    alt.Tooltip("peak_value:Q", title="Peak (m/s²)", format=".2f"),
                ],
            )
            chart = path + markers

        st.altair_chart(chart.properties(height=440).interactive(), width="stretch")

    with events_tab:
        if events.empty:
            st.success("No threshold-exceeding driving events detected")
        else:
            table = events.assign(duration_sec=events["end_time_sec"] - events["start_time_sec"])
            st.dataframe(
                table[["event_label", "start_time_sec", "end_time_sec", "duration_sec", "peak_value"]],
                hide_index=True,
                width="stretch",
                column_config={
                    "event_label": st.column_config.TextColumn("Event"),
                    "start_time_sec": st.column_config.NumberColumn("Start (s)", format="%.2f"),
                    "end_time_sec": st.column_config.NumberColumn("End (s)", format="%.2f"),
                    "duration_sec": st.column_config.NumberColumn("Duration (s)", format="%.2f"),
                    "peak_value": st.column_config.NumberColumn("Peak (m/s²)", format="%.2f"),
                },
            )


def main():
    st.set_page_config(
        page_title="Driving Behavior Analytics",
        page_icon="🚙",
        layout="wide",
    )

    st.title("Driving Behavior Analytics")
    st.caption("Trip-level driving insights from processed vehicle location data")

    trips = load_trips()

    if trips.empty:
        st.info("No analyzed trips found. Run the ingestion and analysis steps first.")
        st.stop()

    # Sidebar navigation
    st.sidebar.header("🚙 VL Analytics")
    driver_ids = sorted(trips["driver_id"].unique().tolist())
    selected_driver = st.sidebar.selectbox(
        "Driver", driver_ids, format_func=lambda driver_id: f"Driver {driver_id}"
    )
    driver_trips = trips[trips["driver_id"] == selected_driver]

    view = st.sidebar.radio("View", ["Trip analysis", "Driving history"])

    if view == "Driving history":
        show_history(driver_trips)
        return

    trip_ids = driver_trips["trip_id"].tolist()
    selected_trip_id = st.sidebar.selectbox(
        "Trip",
        trip_ids,
        format_func=lambda trip_id: (
            f"#{trip_id} · "
            + str(driver_trips.loc[driver_trips["trip_id"] == trip_id, "source_scenario"].iloc[0]).title()
        ),
    )
    st.sidebar.divider()
    st.sidebar.caption("Scores are illustrative and based on event frequency per km.")

    show_trip(driver_trips[driver_trips["trip_id"] == selected_trip_id].iloc[0])


if __name__ == "__main__":
    main()
