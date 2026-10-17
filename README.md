# Vehicle Location Analytics

A data pipeline that transforms simulated vehicle-location (VL) point clouds into driving behavior insights. It includes a WebSocket source, ingestion and analysis stages, a SQLite warehouse, and a Streamlit dashboard.

## Proposed Use Cases

### 1. Driving Behavior Analytics — Implemented
Estimate speed and acceleration from timestamped positions, detect harsh braking, rapid acceleration and hard cornering, and compare trips using an explainable driving score. Potential users include drivers and fleet managers.

### 2. Road Health Intelligence
Detect potential potholes and uneven road segments from unusual vertical (Z-axis) motion across multiple drives. Potential users include municipalities and road maintenance teams.

### 3. Route Pattern Discovery
Identify recurring routes, destinations and travel patterns from historical vehicle trajectories. Potential users include navigation and transportation services.

### 4. Intersection Risk Analyzer
Find locations with recurring harsh braking and turning events to help prioritize further road-safety investigations. Potential users include transportation authorities.

## Implemented Product

The system extracts each frame's 3D centroid, calculates motion from consecutive XY positions, and detects three event types: **rapid acceleration, harsh braking, and hard cornering**. Event detection uses **hysteresis** (entry threshold 3.0 m/s², exit threshold 2.95 m/s²) to group consecutive qualifying frames into events.

Each trip includes distance, duration, average and maximum speed, event counts, and an illustrative driving score. For each event category:

```text
category_score = 100 * max(0, 1 - events_per_km / 5)
overall_score = mean(acceleration_score, braking_score, cornering_score)
```

Scores are not assigned to trips shorter than 500 m. The score measures **event frequency per kilometer**, not event severity.

## Architecture and Design

```text
motion.py / generator.py → FastAPI WebSocket (main.py)
                                  ↓
                         ingest.py (centroids)
                                  ↓
                      SQLite: trips, frame_positions
                                  ↓
                    analyze.py (motion + events)
                                  ↓
                 SQLite: frame_metrics, driving_events
                                  ↓
                         SQL view: trip_summary
                                  ↓
                       Streamlit (dashboard.py)
```

A frame contains roughly one million 3D points. Rather than storing entire clouds, ingestion retains a compact position per frame. The separate `frame_metrics` table stores calculated speed, heading and acceleration. `driving_events` stores event intervals and peak values. The `trips` table records driver, scenario provenance and processing status. `trip_summary` provides queryable trip statistics and scores.

This separation allows reanalysis from stored positions without replaying point clouds. `--events-only` re-detects events from stored motion metrics without recalculating kinematics. The mock cloud translates without rotating, horizontal motion drives the current metrics, while Z positions are retained.

Each frame is sent as JSON metadata followed by a binary point cloud. After the last frame, the server sends {"type": "end", "frame_count": N}. Ingestion requires this message, verifies the received count and consecutive frame IDs starting at zero, and only then allows persistence. A connection close alone does not count as successful completion.

## Setup and Usage
**Python:** Developed and tested on Python 3.13. The code syntax requires Python 3.10+.

Run the commands from the project directory. On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

**1. Start the source server** (keep this terminal open):

```powershell
python -m uvicorn main:app --reload
```

**2. Ingest trips** in another terminal:

```powershell
python ingest.py --scenario calm --driver 1
python ingest.py --scenario mixed --driver 1
python ingest.py --scenario aggressive --driver 2
```

**3. Analyze** the trip IDs returned by ingestion (for a fresh database, they are 1, 2 and 3):

```powershell
python analyze.py --trip 1
python analyze.py --trip 2
python analyze.py --trip 3
```

To recalculate events after changing thresholds:

```powershell
python analyze.py --trip 3 --events-only
```

**4. Open the dashboard:**

```powershell
streamlit run dashboard.py
```

The dashboard has two views, selected in the sidebar:

- **Trip analysis:** key trip metrics, category scores, and three tabs: **Motion** (speed and acceleration over time, with detected events shaded and detection thresholds marked), **Trajectory** (XY path with event locations) and **Events** (table of detected events).
- **Driving history:** all analyzed trips of the selected driver, with the driving score per trip and a comparison table.

Data is stored in a local `cloud.db`, created during ingestion.

### Example warehouse query

After analysis, `trip_summary` can be queried directly in SQLite. For example, compare analyzed trips by driving score:

```sql
SELECT
    trip_id,
    driver_id,
    ROUND(distance_m / 1000.0, 2) AS distance_km,
    rapid_acceleration_count,
    harsh_braking_count,
    hard_cornering_count,
    ROUND(overall_score, 1) AS overall_score
FROM trip_summary
WHERE overall_score IS NOT NULL
ORDER BY overall_score DESC;
```

## Validation and Tests

Before analysis, stored data is checked for at least three frames, consecutive frame IDs, increasing timestamps and finite coordinates. Motion metrics are checked for valid values, allowing expected missing values at startup or while stationary.

Run the automated tests from the project directory:

```powershell
python -m unittest -v test_pipeline
```

The 21 tests cover motion calculations, event detection, SQL scores, invalid inputs, repeated analysis and stream completion. Tests use in-memory databases and small inputs without modifying `cloud.db`. Full-size streams are checked separately: with the server running, `python check_stream.py` streams the complete aggressive trip through the same validation as ingestion and prints the average frames per second.

## Error Codes

Expected pipeline failures produce a clear error code and a non-zero process exit status.

| Code | Description |
| --- | --- |
| `VL-101` | Cannot connect to the WebSocket source |
| `VL-102` | Stream closed before completion, ended mid-frame, or final frame count mismatched |
| `VL-103` | Invalid frame metadata, sequence, payload or end-message format |
| `VL-104` | No frames received |
| `VL-201` | Requested trip has no stored positions |
| `VL-202` | Invalid or non-increasing frame timestamps |
| `VL-203` | Missing or incomplete metrics for event-only analysis |
| `VL-204` | Fewer than three frames for analysis |
| `VL-205` | Invalid stored coordinates or frame sequence |
| `VL-206` | Invalid or unexpectedly missing motion metrics |
| `VL-301` | SQLite operation failed |

An ingestion failure before persistence produces a CLI error without creating a trip. During analysis, failures for an existing trip can be recorded as `status='failed'` with an `error_code`.

## Release Notes — v1.0

**Product:** End-to-end vehicle-location analytics with simulated point-cloud streaming, reprocessable trip data, event detection and an interactive BI dashboard.

**Usage:** Follow *Setup and Usage* above to generate, analyze and compare trips.

**Known issues:** The simulated data does not model sensor noise or missing points. The point cloud translates without rotating, even during turns. Driving scores and thresholds are illustrative and have not been validated against real-world driving safety. Position-based derivatives are sensitive to numerical noise near thresholds. The mock source streams frames as fast as possible; timestamps simulate 16 FPS, but there is no real-time pacing.

**Coming soon:** Broader validation with noisy and irregularly sampled trajectories, speed-limit context and analysis of vertical (Z-axis) motion.

## LLM Usage

**Tool/model:** OpenAI ChatGPT (GPT-6)

ChatGPT was used as a technical assistant during development: exploring implementation options, reviewing code, clarifying Python/SQLite/WebSocket behavior, and debugging integration issues. The development process involved comparing design alternatives, running the implementation locally, inspecting outputs, and refining the solution based on observed behavior.

**Representative main prompts:**

- How can dense VL point-cloud data be reduced into a compact, queryable data warehouse?
- How should ingestion, kinematic calculations and event detection be separated to support reprocessing?
- How can hysteresis stabilize event detection, and how should threshold-boundary cases be handled?
- How can trip statistics be calculated in SQLite and checked against Python results?
- How should pipeline error codes and failure handling be implemented?
- How can Streamlit present trip metrics and comparisons clearly?

**Tool/model:** Anthropic Claude (Claude Opus 5.5)

Claude was used as a project reviewer during the later stages of development: for code review and dashboard styling.

The resulting design choices and behavior were reviewed through local execution, debugging and automated tests with analytical expectations and simulator scenarios.
