import glob
import gzip
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
from engine import Engine, Network, align, dwell_windows, meetings, parse_monitor, pos_at  # noqa: E402

NET = json.load(open(os.path.join(HERE, '..', 'data', 'network.json')))


def synthetic_network():
    """One line U9, 4 stations 1000 m apart, 100 s between departures, 25 s dwell."""
    sts = [{'diva': i, 'name': 'S%d' % i, 'rbl': 100 + i if i < 3 else None, 'lat': 48.0, 'lon': 16.0 + i * 0.01,
            's': i * 1000.0, 'dep2dep': 100.0 if i < 3 else None} for i in range(4)]
    path = [[48.0, 16.0], [48.0, 16.03]]
    data = {'lines': {'U9': {'color': '#000', 'dirs': {'H': {
        'towards': 'S3', 'dwell': 25, 'stations': sts, 'path': path, 'cum': [0.0, 3000.0]}}}}}
    return Network(data)


def monitor(rbl_deps, now):
    """Builds a minimal API payload: {rbl: [seconds from now]}"""
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=2))
    mons = []
    for rbl, secs in rbl_deps.items():
        deps = [{'departureTime': {'timeReal': datetime.fromtimestamp(now + s, tz).strftime('%Y-%m-%dT%H:%M:%S.000%z')}}
                for s in secs]
        mons.append({'locationStop': {'properties': {'attributes': {'rbl': rbl}}},
                     'lines': [{'name': 'U9', 'direction': 'H', 'towards': 'S3', 'departures': {'departure': deps}}]})
    return {'data': {'monitors': mons}}


class AlignTests(unittest.TestCase):
    def test_matches_in_order(self):
        self.assertEqual(align([0, 300], [105, 410], 100), [(0, 0), (1, 1)])

    def test_unmatched_when_out_of_tolerance(self):
        self.assertEqual(align([0, 300], [310], 0), [(1, 0)])


class SyntheticTests(unittest.TestCase):
    def setUp(self):
        self.net = synthetic_network()
        self.now = 1_800_000_000.0

    def run_engine(self, deps):
        obs = parse_monitor(monitor(deps, self.now))
        return Engine(self.net).update(obs, self.now)

    def test_train_between_stations(self):
        # departs S1 in 60 s: 25 s dwell, so it arrives in 35 s; 75 s moving time -> 40 s underway
        trains = self.run_engine({101: [60.0], 102: [160.0]})
        self.assertEqual(len(trains), 1)
        t = trains[0]
        self.assertFalse(t['at_station'])
        self.assertEqual((t['prev'], t['next']), ('S0', 'S1'))
        self.assertAlmostEqual(t['s'], 1000 * (1 - (60 - 25) / 75.0), delta=5)

    def test_train_in_station(self):
        trains = self.run_engine({101: [10.0], 102: [110.0]})
        self.assertTrue(trains[0]['at_station'])
        self.assertEqual(trains[0]['next'], 'S1')

    def test_two_trains_are_two_trains(self):
        trains = self.run_engine({101: [60.0, 150.0], 102: [160.0, 250.0]})
        self.assertEqual(len(trains), 2)
        self.assertNotEqual(trains[0]['s'], trains[1]['s'])

    def test_far_future_train_hidden(self):
        self.assertEqual(self.run_engine({101: [900.0], 102: [1000.0]}), [])

    def test_runs_on_to_terminal_without_data(self):
        trains = self.run_engine({102: [20.0]})   # last station with data; terminal S3 has no rbl
        self.assertEqual(len(trains), 1)
        self.assertAlmostEqual(trains[0]['wp'][-1][1], 3000.0, delta=1)  # timeline ends at the terminal

    def test_ghost_survives_until_arrival(self):
        eng = Engine(self.net)
        eng.update(parse_monitor(monitor({102: [20.0]}, self.now)), self.now)
        later = self.now + 60                 # S2 already left, no station reports the train any more
        trains = eng.update({}, later)
        self.assertEqual(len(trains), 1)
        self.assertTrue(trains[0]['ghost'])
        self.assertEqual(eng.update({}, self.now + 400), [])

    def test_identity_is_kept(self):
        eng = Engine(self.net)
        a = eng.update(parse_monitor(monitor({101: [60.0], 102: [160.0]}, self.now)), self.now)
        b = eng.update(parse_monitor(monitor({101: [30.0], 102: [130.0]}, self.now + 30)), self.now + 30)
        self.assertEqual(a[0]['id'], b[0]['id'])


class MeetingTests(unittest.TestCase):
    def test_dwell_windows(self):
        wp = [(100, 0.0), (130, 500.0), (155, 500.0), (200, 900.0)]
        self.assertEqual(dwell_windows(wp, 500.0, pad=0), [(130, 155)])
        self.assertEqual(dwell_windows([(0, 5.0), (30, 5.0)], 5.0, pad=10), [(-10, 40)])

    def test_direct_transfer_needs_same_direction(self):
        net = Network(NET)
        sa = next(x['s'] for x in net.tracks[('U4', 'R')].st if x['name'] == 'Längenfeldgasse')
        sb = next(x['s'] for x in net.tracks[('U6', 'R')].st if x['name'] == 'Längenfeldgasse')
        mk = lambda line, d, s, t0, t1: {'line': line, 'dir': d, 'ghost': False, 'wp': [(t0, s), (t1, s)]}
        both = meetings(net, [mk('U4', 'R', sa, 100, 125), mk('U6', 'R', sb, 110, 135)], 'U4', 'U6', 'Längenfeldgasse')
        self.assertEqual(len(both['R']['meetings']), 1)
        self.assertEqual(both['H']['meetings'], [])
        sh = next(x['s'] for x in net.tracks[('U6', 'H')].st if x['name'] == 'Längenfeldgasse')
        wrong = meetings(net, [mk('U4', 'R', sa, 100, 125), mk('U6', 'H', sh, 110, 135)], 'U4', 'U6', 'Längenfeldgasse')
        self.assertEqual(wrong['R']['meetings'] + wrong['H']['meetings'], [])


class ReplayTests(unittest.TestCase):
    """Replays recorded live responses (tests/fixtures/*.json.gz) - works at night, too."""

    @classmethod
    def setUpClass(cls):
        cls.files = sorted(glob.glob(os.path.join(HERE, 'fixtures', '*.json.gz')))

    def replay(self):
        eng = Engine(Network(NET))
        for f in self.files:
            rec = json.load(gzip.open(f, 'rt'))
            obs = {}
            for payload in rec['responses']:
                for k, v in parse_monitor(payload).items():
                    obs.setdefault(k, []).extend(v)
            yield rec['ts'], eng.update(obs, rec['ts'])

    def test_plausible_network(self):
        if not self.files:
            self.skipTest('no fixtures')
        for ts, trains in self.replay():
            self.assertGreater(len(trains), 30)
            lines = {t['line'] for t in trains}
            self.assertTrue({'U1', 'U3', 'U4', 'U6'} <= lines, lines)

    def test_no_duplicates(self):
        for ts, trains in self.replay():
            seen = {}
            for t in trains:
                if t['s'] < 50:      # two trains may stand at the terminal at once
                    continue
                for o in seen.get((t['line'], t['dir']), []):
                    self.assertGreater(abs(o - t['s']), 120, 'two trains on top of each other: %s' % t)
                seen.setdefault((t['line'], t['dir']), []).append(t['s'])

    def test_identities_are_stable(self):
        prev = None
        kept = total = 0
        for ts, trains in self.replay():
            cur = {t['id']: t for t in trains}
            if prev:
                total += len(prev)
                kept += sum(1 for i in prev if i in cur)
                for i, t in cur.items():
                    if i in prev:
                        # moved forward by a plausible amount (speed <= ~90 km/h)
                        self.assertLess(abs(t['s'] - prev[i]['s']), 25 * (ts - prev_ts) + 300)
            prev, prev_ts = cur, ts
        if total:
            self.assertGreater(kept / total, 0.85)


if __name__ == '__main__':
    unittest.main()
