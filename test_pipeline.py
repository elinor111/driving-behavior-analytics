"""Run with: python -m unittest -v test_pipeline
Uses temporary in-memory databases and small synthetic inputs; never edits cloud.db.
"""

import asyncio
import contextlib
import io
import json
import math
import sqlite3
import unittest
from unittest.mock import patch

import numpy as np
from websockets.exceptions import ConnectionClosedOK
from websockets.frames import Close

import ingest
import main as server
from analyze import analyze_trip, mark_trip_failed
from database import create_tables, save_positions
from errors import PipelineError
from motion import SCENARIOS, VehicleState, simulate_scenario, update_state
from sql_views import create_views
from transform import MotionProcessor, EventDetector, calculate_lateral_acceleration


class MotionTests(unittest.TestCase):
    def process(self, positions):
        processor = MotionProcessor()
        return [processor.process_position(np.array([x, y, 0.0]), t)
                for t, x, y in positions]

    def test_constant_speed(self):
        rows = self.process([(0, 0, 0), (1, 3, 4), (2, 6, 8)])
        self.assertIsNone(rows[0]['speed'])
        self.assertIsNone(rows[1]['acceleration'])
        self.assertEqual(rows[2]['speed'], 5)
        self.assertEqual(rows[2]['acceleration'], 0)
        self.assertAlmostEqual(rows[2]['lateral_acceleration'], 0)

    def test_constant_acceleration_and_braking(self):
        for a, v in [(2, 0), (-2, 10)]:
            with self.subTest(acceleration=a):
                rows = self.process([(t, v*t + 0.5*a*t*t, 0) for t in range(4)])
                self.assertAlmostEqual(rows[1]['speed'], v + a/2)
                for row in rows[2:]: self.assertAlmostEqual(row['acceleration'], a)

    def test_circular_motion(self):
        radius, omega, dt = 10, 0.2, 0.1
        rows = self.process([(i*dt, radius*math.cos(omega*i*dt),
                              radius*math.sin(omega*i*dt)) for i in range(5)])
        # Chord speed is 2R sin(omega*dt/2)/dt, not the instantaneous arc speed.
        expected = 2*radius*math.sin(omega*dt/2)/dt*omega
        for row in rows[2:]:
            self.assertAlmostEqual(row['lateral_acceleration'], expected)

    def test_heading_wrap(self):
        self.assertAlmostEqual(calculate_lateral_acceleration(
            math.radians(179), math.radians(-179), 10, 1), 10*math.radians(2))

    def test_stationary(self):
        rows = self.process([(0, 0, 0), (1, 0, 0), (2, 0, 0)])
        self.assertEqual(rows[2]['speed'], 0)
        self.assertIsNone(rows[2]['heading'])
        self.assertIsNone(rows[2]['lateral_acceleration'])

    def test_stop_inside_step(self):
        state = update_state(VehicleState(speed=1), -2, 0, 1)
        self.assertEqual(state.speed, 0)
        self.assertAlmostEqual(state.x, 0.25)

    def test_event_hysteresis_and_finalize(self):
        for kind, sign, lateral in [('rapid_acceleration', 1, False),
                                    ('harsh_braking', -1, False),
                                    ('hard_cornering', -1, True)]:
            with self.subTest(kind=kind):
                detector = EventDetector()
                for t, value in enumerate([3.0, 3.2, 2.97, 2.95, 2.94, 3.5]):
                    detector.process_frame({'timestamp': t,
                        'acceleration': 0 if lateral else sign*value,
                        'lateral_acceleration': sign*value if lateral else 0})
                detector.finalize()
                detector.finalize()  # Must not duplicate finished events.
                self.assertEqual(len(detector.events), 2)
                self.assertEqual(detector.events[0]['type'], kind)
                self.assertEqual((detector.events[0]['start_time'], detector.events[0]['end_time']), (1, 3))
                self.assertEqual(detector.events[1]['peak_value'], sign*3.5)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        create_tables(self.conn)
        create_views(self.conn)
        self.addCleanup(self.conn.close)

    def trip(self, xs=(0, 5, 10), times=None):
        if times is None: times = list(range(len(xs)))
        with self.conn:
            return save_positions(self.conn, 1, [dict(frame_id=i, timestamp=t, x=x, y=0, z=0)
                for i, (t, x) in enumerate(zip(times, xs))])

    def assert_code(self, code, action):
        with self.assertRaises(PipelineError) as error: action()
        self.assertEqual(error.exception.code, code)

    def test_missing_trip(self):
        self.assert_code('VL-201', lambda: analyze_trip(self.conn, 99))

    def test_short_trip_both_modes(self):
        for n in (1, 2):
            trip = self.trip(tuple(range(n)))
            for mode in (False, True):
                self.assert_code('VL-204', lambda: analyze_trip(self.conn, trip, mode))

    def test_bad_timestamps(self):
        for times in ([0, 0, 1], [0, 2, 1], [0, 1, float('inf')], [0, 1, 'bad']):
            trip = self.trip(times=times)
            self.assert_code('VL-202', lambda: analyze_trip(self.conn, trip))

    def test_bad_coordinates_and_sequence(self):
        for value in (float('inf'), 'bad'):
            trip = self.trip((0, value, 10))
            for mode in (False, True):
                self.assert_code('VL-205', lambda: analyze_trip(self.conn, trip, mode))
        trip = self.trip()
        self.conn.execute('UPDATE frame_positions SET frame_id=3 WHERE trip_id=? AND frame_id=2', (trip,))
        self.conn.commit()
        self.assert_code('VL-205', lambda: analyze_trip(self.conn, trip))

    def test_missing_stored_metrics(self):
        trip = self.trip()
        self.assert_code('VL-203', lambda: analyze_trip(self.conn, trip, True))

    def test_corrupt_metrics_do_not_replace_events(self):
        for field, value, frame in [('speed_mps', -1, 2), ('speed_mps', None, 2),
                ('acceleration_mps2', float('inf'), 2), ('acceleration_mps2', None, 2),
                ('heading', None, 2), ('lateral_acceleration_mps2', None, 2),
                ('speed_mps', 1, 0), ('heading', 'bad', 2)]:
            with self.subTest(field=field, value=value, frame=frame):
                trip = self.trip((0, 2, 8, 18))
                analyze_trip(self.conn, trip)
                before = self.conn.execute('SELECT * FROM driving_events WHERE trip_id=?', (trip,)).fetchall()
                self.assertTrue(before)
                self.conn.execute(f'UPDATE frame_metrics SET {field}=? WHERE trip_id=? AND frame_id=?', (value, trip, frame))
                self.conn.commit()
                self.assert_code('VL-206', lambda: analyze_trip(self.conn, trip, True))
                self.assertEqual(before, self.conn.execute('SELECT * FROM driving_events WHERE trip_id=?', (trip,)).fetchall())

    def test_stationary_trip_and_reanalysis(self):
        trip = self.trip((0, 0, 0))
        first = analyze_trip(self.conn, trip)
        self.assertEqual(first['distance_m'], 0)
        self.assertIsNone(first['overall_score'])
        self.assertEqual(first, analyze_trip(self.conn, trip, True))

    def test_sql_score_boundaries(self):
        # Independent, hand-calculated expectations: 1 acceleration event.
        for distance, score in [(499, None), (500, 60), (1000, 80)]:
            trip = self.trip((0, distance/2, distance))
            analyze_trip(self.conn, trip)
            with self.conn:
                self.conn.execute('DELETE FROM driving_events WHERE trip_id=?', (trip,))
                self.conn.execute("INSERT INTO driving_events (trip_id,event_type,start_time_sec,end_time_sec,peak_value) VALUES (?,'rapid_acceleration',1,1,4)", (trip,))
            actual, overall = self.conn.execute('SELECT acceleration_score,overall_score FROM trip_summary WHERE trip_id=?', (trip,)).fetchone()
            self.assertEqual(actual, score)
            if score is None: self.assertIsNone(overall)
            else: self.assertAlmostEqual(overall, (score+200)/3)
        with self.conn:
            for _ in range(5):
                self.conn.execute("INSERT INTO driving_events (trip_id,event_type,start_time_sec,end_time_sec,peak_value) VALUES (?,'rapid_acceleration',1,1,4)", (trip,))
        self.assertEqual(self.conn.execute('SELECT acceleration_score FROM trip_summary WHERE trip_id=?', (trip,)).fetchone()[0], 0)

    def test_scenarios_and_repeat_analysis(self):
        expected = {'calm': (0, 0, 0), 'mixed': (1, 1, 0), 'aggressive': (2, 2, 2)}
        for name, counts in expected.items():
            trajectory = simulate_scenario(SCENARIOS[name])
            with self.conn:
                trip = save_positions(self.conn, 1, [dict(frame_id=i, timestamp=t, x=s.x, y=s.y, z=0)
                    for i, (t, s) in enumerate(trajectory)], name)
            first = analyze_trip(self.conn, trip)
            self.assertEqual(tuple(first[k] for k in ('rapid_acceleration_count','harsh_braking_count','hard_cornering_count')), counts)
            metrics = self.conn.execute('SELECT * FROM frame_metrics WHERE trip_id=?', (trip,)).fetchall()
            self.assertEqual(first, analyze_trip(self.conn, trip, True))
            self.assertEqual(metrics, self.conn.execute('SELECT * FROM frame_metrics WHERE trip_id=?', (trip,)).fetchall())
            self.assertEqual(first, analyze_trip(self.conn, trip))

    def test_failure_status(self):
        trip = self.trip((0,))
        mark_trip_failed(self.conn, trip, 'VL-204')
        self.assertEqual(self.conn.execute('SELECT status,error_code FROM trips WHERE trip_id=?', (trip,)).fetchone(), ('failed','VL-204'))


class FakeSocket:
    """Small in-memory transport; normal close deliberately looks successful."""
    def __init__(self, messages=()):
        self.messages = iter(messages)
        self.sent = []
        self.query_params = {'scenario': 'calm'}
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def accept(self): pass
    async def recv(self):
        try: return next(self.messages)
        except StopIteration:
            raise ConnectionClosedOK(Close(1000, ''), Close(1000, ''), True)
    async def send_json(self, message): self.sent.append(json.dumps(message))
    async def send_bytes(self, message): self.sent.append(message)
    async def close(self, **kwargs): pass


def frame(index=0):
    cloud = np.array([[index, 0, 0]], dtype=np.float32)
    return [json.dumps(dict(frame_id=index, timestamp=index/16, dtype='float32', shape=[1,3])), cloud.tobytes()]


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def receive(self, messages):
        with patch.object(ingest, 'connect', return_value=FakeSocket(messages)), contextlib.redirect_stdout(io.StringIO()):
            return await ingest.ingest_trip()

    async def test_complete_stream(self):
        result = await self.receive(frame() + [json.dumps(dict(type='end', frame_count=1))])
        self.assertEqual(result['frame_count'], 1)

    async def test_rejected_streams(self):
        cases = [(frame(), 'VL-102'), (frame()[:1], 'VL-102'),
            (frame()+[json.dumps(dict(type='end', frame_count=2))], 'VL-102'),
            (frame()+[json.dumps(dict(type='end', frame_count=True))], 'VL-103'),
            ([json.dumps(dict(type='end', frame_count=0))], 'VL-104'),
            (frame(1), 'VL-103'), (frame()+frame(2), 'VL-103'),
            (['bad-json'], 'VL-103'), (frame()[:1]+[b'bad'], 'VL-103')]
        for messages, code in cases:
            with self.subTest(code=code, messages=messages):
                with self.assertRaises(PipelineError) as error: await self.receive(messages)
                self.assertEqual(error.exception.code, code)

    async def test_server_and_reader_agree(self):
        async def small_source(scenario):
            for i in range(3):
                yield dict(frame_id=i, timestamp=i/16, points=np.array([[i,0,0]], dtype=np.float32))
        socket = FakeSocket()
        with patch.object(server, 'gen_cloud', small_source): await server.stream_cloud(socket)
        self.assertEqual(json.loads(socket.sent[-1]), dict(type='end',frame_count=3))
        self.assertEqual((await self.receive(socket.sent))['frame_count'], 3)

    async def test_generator_failure_has_no_completion(self):
        async def broken_source(scenario):
            yield dict(frame_id=0,timestamp=0,points=np.zeros((1,3),dtype=np.float32))
            raise RuntimeError('source failed')
        socket = FakeSocket()
        with patch.object(server, 'gen_cloud', broken_source):
            with self.assertRaises(RuntimeError): await server.stream_cloud(socket)
        with self.assertRaises(PipelineError) as error: await self.receive(socket.sent)
        self.assertEqual(error.exception.code, 'VL-102')


if __name__ == '__main__':
    unittest.main()
