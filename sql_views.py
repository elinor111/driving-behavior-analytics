
import sqlite3


def create_views(conn: sqlite3.Connection) -> None:
    """ Create SQL views for trip-level driving statistics """

    with conn:
        conn.execute("DROP VIEW IF EXISTS trip_summary")

        conn.execute("""
            CREATE VIEW trip_summary AS
            WITH steps AS (
                SELECT
                    trip_id,
                    frame_id,
                    elapsed_sec,
                    x,
                    y,
                    LAG(x) OVER (
                        PARTITION BY trip_id ORDER BY frame_id
                    ) AS previous_x,
                    LAG(y) OVER (
                        PARTITION BY trip_id ORDER BY frame_id
                    ) AS previous_y
                FROM frame_positions
            ),

            distances AS (
                SELECT
                    trip_id,
                    SUM(
                        CASE
                            WHEN previous_x IS NULL THEN 0.0
                            ELSE SQRT(
                                (x - previous_x) * (x - previous_x)
                                + (y - previous_y) * (y - previous_y)
                            )
                        END
                    ) AS distance_m,
                    MAX(elapsed_sec) - MIN(elapsed_sec) AS duration_sec
                FROM steps
                GROUP BY trip_id
            ),

            speeds AS (
                SELECT
                    trip_id,
                    MAX(speed_mps) * 3.6 AS max_speed_kmh
                FROM frame_metrics
                GROUP BY trip_id
            ),

            event_counts AS (
                SELECT
                    trip_id,
                    SUM(CASE WHEN event_type = 'rapid_acceleration'
                        THEN 1 ELSE 0 END) AS rapid_acceleration_count,
                    SUM(CASE WHEN event_type = 'harsh_braking'
                        THEN 1 ELSE 0 END) AS harsh_braking_count,
                    SUM(CASE WHEN event_type = 'hard_cornering'
                        THEN 1 ELSE 0 END) AS hard_cornering_count
                FROM driving_events
                GROUP BY trip_id
            ),

            aggregated AS (
                SELECT
                    t.trip_id,
                    t.driver_id,
                    t.recorded_at,
                    t.source_scenario,
                    d.duration_sec,
                    d.distance_m,
                    CASE
                        WHEN d.duration_sec > 0
                        THEN 3.6 * d.distance_m / d.duration_sec
                        ELSE NULL
                    END AS avg_speed_kmh,
                    s.max_speed_kmh,

                    COALESCE(e.rapid_acceleration_count, 0)
                        AS rapid_acceleration_count,
                    COALESCE(e.harsh_braking_count, 0)
                        AS harsh_braking_count,
                    COALESCE(e.hard_cornering_count, 0)
                        AS hard_cornering_count

                FROM trips AS t
                JOIN distances AS d ON d.trip_id = t.trip_id
                LEFT JOIN speeds AS s ON s.trip_id = t.trip_id
                LEFT JOIN event_counts AS e ON e.trip_id = t.trip_id

                WHERE t.status = 'analyzed'
            ),

            -- Scores: 100 * max(0, 1 - events_per_km / 5); no score below 500 m (see README)
            scored AS (
                SELECT *,
                    CASE WHEN distance_m < 500 THEN NULL
                        ELSE 100.0 * MAX(
                            0.0,
                            1.0 - rapid_acceleration_count * 1000.0
                                / distance_m / 5.0
                        )
                    END AS acceleration_score,

                    CASE WHEN distance_m < 500 THEN NULL
                        ELSE 100.0 * MAX(
                            0.0,
                            1.0 - harsh_braking_count * 1000.0
                                / distance_m / 5.0
                        )
                    END AS braking_score,

                    CASE WHEN distance_m < 500 THEN NULL
                        ELSE 100.0 * MAX(
                            0.0,
                            1.0 - hard_cornering_count * 1000.0
                                / distance_m / 5.0
                        )
                    END AS cornering_score
                FROM aggregated
            )

            SELECT *,
                (
                    acceleration_score
                    + braking_score
                    + cornering_score
                ) / 3.0 AS overall_score
            FROM scored
        """)


