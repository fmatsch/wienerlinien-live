"""Derives U-Bahn train positions from Wiener Linien departure forecasts.

The monitor API only returns departure times per platform (RBL). A train that
will depart station i at t_i departs station i+1 at roughly t_i + dep2dep[i].
Chaining the departures of neighbouring stations therefore reconstructs
individual trains; the head of each chain (first station where the train has
not left yet) pins down where the train currently is.

Everything here is pure (no I/O) so it can be tested with recorded responses.
"""
import bisect
import math
from datetime import datetime

MATCH_TOL = 110.0        # s, max deviation when chaining departures of neighbouring stations
WAIT_AT_TERMINAL = 240.0  # s, show a train waiting at the first station this long before departure
LATE_SLACK = 90.0        # s, tolerated overshoot before a train is hidden instead of clamped
PAST_SLACK = 45.0        # s, a forecast departure this far in the past is still accepted (dwelling)
TRACK_MATCH_M = 450.0    # m, max distance to continue a train identity across polls
GHOST_MAX_AGE = 420.0    # s
HORIZON = 600.0          # s of waypoints sent to the client


# ---------------------------------------------------------------- parsing

def parse_time(s):
    return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S.%f%z').timestamp()


def parse_monitor(payload, wanted=lambda name: name.startswith('U')):
    """API JSON -> {(rbl, line, dir): [(epoch, towards, on_stop), ...]} (sorted)."""
    out = {}
    for mon in (payload.get('data') or {}).get('monitors', []):
        rbl = mon['locationStop']['properties']['attributes'].get('rbl')
        for ln in mon.get('lines', []):
            name = ln.get('name', '')
            if not wanted(name):
                continue
            deps = []
            for d in (ln.get('departures') or {}).get('departure', []):
                dt = d.get('departureTime') or {}
                ts = dt.get('timeReal') or dt.get('timePlanned')
                if not ts:
                    continue
                veh = d.get('vehicle') or {}
                deps.append((parse_time(ts), veh.get('towards') or ln.get('towards'), veh.get('onStop'),
                             bool(dt.get('timeReal'))))
            deps.sort()
            out.setdefault((rbl, name, ln.get('direction')), []).extend(deps)
    return out


# ---------------------------------------------------------------- geometry

class Track:
    """One line/direction: stations, polyline, arc length lookup."""

    def __init__(self, line, d, data):
        self.line, self.dir = line, d
        self.st = data['stations']
        self.n = len(self.st)
        self.dwell = data['dwell']
        self.dep2dep = [s['dep2dep'] for s in self.st]
        self.path = data['path']
        self.cum = data['cum']
        self.towards = data['towards']

    def latlon(self, s):
        i = max(1, min(bisect.bisect_left(self.cum, s), len(self.cum) - 1))
        a, b = self.cum[i - 1], self.cum[i]
        f = 0 if b == a else (s - a) / (b - a)
        p, q = self.path[i - 1], self.path[i]
        return p[0] + f * (q[0] - p[0]), p[1] + f * (q[1] - p[1])

    def bearing(self, s):
        i = max(1, min(bisect.bisect_left(self.cum, s), len(self.cum) - 1))
        p, q = self.path[i - 1], self.path[i]
        dy = q[0] - p[0]
        dx = (q[1] - p[1]) * math.cos(math.radians(p[0]))
        return (math.degrees(math.atan2(dx, dy)) + 360) % 360


class Network:
    def __init__(self, data):
        self.tracks = {}
        self.rbl_index = {}   # (rbl, line, dir) -> (track key, station idx)
        for line, v in data['lines'].items():
            for d, td in v['dirs'].items():
                t = Track(line, d, td)
                t.color = v['color']
                self.tracks[(line, d)] = t
                for i, s in enumerate(t.st):
                    if s['rbl']:
                        self.rbl_index[(s['rbl'], line, d)] = ((line, d), i)

    def tracks_lines(self):
        return {k[0] for k in self.tracks}

    def rbls(self, lines=None):
        return sorted({k[0] for k in self.rbl_index if lines is None or k[1] in lines})


# ---------------------------------------------------------------- matching

def align(A, B, delta):
    """Monotone one-to-one matching of times A+delta to times B (both sorted), minimising total error.
    Returns list of (ai, bj)."""
    na, nb = len(A), len(B)
    INF = 1e18
    # dp[i][j] = best cost using first i of A and first j of B
    SKIP = MATCH_TOL * 1.0  # cost of leaving one element unmatched
    dp = [[0.0] * (nb + 1) for _ in range(na + 1)]
    for i in range(1, na + 1):
        dp[i][0] = i * SKIP
    for j in range(1, nb + 1):
        dp[0][j] = j * SKIP
    for i in range(1, na + 1):
        for j in range(1, nb + 1):
            best = min(dp[i - 1][j], dp[i][j - 1]) + SKIP
            err = abs(A[i - 1] + delta - B[j - 1])
            if err <= MATCH_TOL:
                best = min(best, dp[i - 1][j - 1] + err)
            dp[i][j] = best
    pairs, i, j = [], na, nb
    while i > 0 and j > 0:
        err = abs(A[i - 1] + delta - B[j - 1])
        if err <= MATCH_TOL and abs(dp[i][j] - (dp[i - 1][j - 1] + err)) < 1e-9:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif abs(dp[i][j] - (dp[i - 1][j] + SKIP)) < 1e-9:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def build_chains(track, deps):
    """deps: {station idx: [(t, towards, onstop, real), ...]}  -> list of chains,
    each chain = list of (station idx, t, towards, onstop, real)."""
    idxs = [i for i in range(track.n) if deps.get(i)]
    nxt, has_prev = {}, set()
    for a, b in zip(idxs, idxs[1:]):
        delta = sum(track.dep2dep[a:b])
        A = [d[0] for d in deps[a]]
        B = [d[0] for d in deps[b]]
        for ai, bj in align(A, B, delta):
            nxt[(a, ai)] = (b, bj)
            has_prev.add((b, bj))
    chains = []
    for i in idxs:
        for k, d in enumerate(deps[i]):
            if (i, k) in has_prev:
                continue
            chain, node = [], (i, k)
            while node is not None:
                st, kk = node
                t, tw, on, real = deps[st][kk]
                chain.append((st, t, tw, on, real))
                node = nxt.get(node)
            chains.append(chain)
    return chains


def waypoints(track, chain, now, data_idx):
    """Timeline [(t, s), ...] from now on, plus open_end flag (train runs beyond the last known departure)."""
    st = track.st
    wp = []
    dw = track.dwell
    head_i, head_t = chain[0][0], chain[0][1]
    tau = head_t - now
    # current position
    if head_i == 0:
        s_now = st[0]['s']
        wp.append((now, s_now))
        if head_t > now:
            wp.append((head_t, s_now))
    else:
        seg = track.dep2dep[head_i - 1]
        move = max(seg - dw, 20.0)
        if tau <= dw:
            s_now = st[head_i]['s']
            wp.append((now, s_now))
            wp.append((head_t, s_now))
        else:
            frac = 1.0 - (tau - dw) / move
            frac = max(0.0, min(1.0, frac))
            a, b = st[head_i - 1]['s'], st[head_i]['s']
            wp.append((now, a + frac * (b - a)))
            wp.append((head_t - dw, b))
            wp.append((head_t, b))
    # known departures onwards
    last = chain[0]
    for node in chain[1:]:
        i, t = node[0], node[1]
        prev_i = last[0]
        if i - prev_i == 1:
            wp.append((t - dw, st[i]['s']))
        else:
            # skipped stations without data: straight line between the two data stations
            pass
        wp.append((t, st[i]['s']))
        last = node
    # extension beyond the last known departure
    open_end = False
    i, t = last[0], last[1]
    while i + 1 < track.n:
        t_arr = t + track.dep2dep[i] - dw
        wp.append((t_arr, st[i + 1]['s']))
        open_end = True
        if i + 1 == track.n - 1 or i + 1 in data_idx:
            break  # terminal, or a station with data that has no matching departure -> the train ends there
        t = t_arr + dw
        wp.append((t, st[i + 1]['s']))
        i += 1
    wp = [w for w in wp if w[0] >= now - 1e-6 or w is wp[0]]
    # monotone time & s
    out = []
    for t, s in wp:
        if out and t < out[-1][0]:
            t = out[-1][0]
        out.append((t, s))
    return out, open_end


def pos_at(wp, t):
    if t <= wp[0][0]:
        return wp[0][1]
    if t >= wp[-1][0]:
        return wp[-1][1]
    k = bisect.bisect_right([w[0] for w in wp], t)
    (t0, s0), (t1, s1) = wp[k - 1], wp[k]
    return s0 if t1 == t0 else s0 + (s1 - s0) * (t - t0) / (t1 - t0)


# ---------------------------------------------------------------- main entry

class Engine:
    def __init__(self, network):
        self.net = network
        self.tracks = {}     # id -> dict (previous poll state)
        self.next_id = 1

    def update(self, obs, now, lines=None):
        """obs: output of parse_monitor (merged over all requests). Returns (trains, debug)."""
        by_track = {}
        for (rbl, line, d), deps in obs.items():
            key = self.net.rbl_index.get((rbl, line, d))
            if key and (lines is None or line in lines):
                by_track.setdefault(key[0], {})[key[1]] = deps
        found = []
        dbg_chains = {}
        for tk, deps in by_track.items():
            track = self.net.tracks[tk]
            data_idx = set(deps)
            for ch in build_chains(track, deps):
                head_i, head_t = ch[0][0], ch[0][1]
                tau = head_t - now
                if tau < -PAST_SLACK:
                    continue
                if head_i == 0:
                    if tau > WAIT_AT_TERMINAL:
                        continue
                else:
                    seg = track.dep2dep[head_i - 1]
                    if tau > track.dwell + max(seg - track.dwell, 20.0) + LATE_SLACK:
                        continue  # not on this segment yet: upstream data missing -> hide
                wp, open_end = waypoints(track, ch, now, data_idx)
                found.append({'tk': tk, 'wp': wp, 'chain': ch, 'open_end': open_end, 'head_i': head_i,
                              'head_t': head_t, 'towards': ch[0][2], 'on_stop': ch[0][3], 'real': ch[0][4]})
            dbg_chains[tk] = None
        trains = self._track(found, now, lines)
        return trains

    def _track(self, found, now, lines=None):
        prev = self.tracks
        cand = []
        for fi, f in enumerate(found):
            s_new = pos_at(f['wp'], now)
            for pid, p in prev.items():
                if p['tk'] != f['tk']:
                    continue
                # a train that is held in a station keeps its old position: compare with both the
                # forecast position and the position it was last seen at
                dm = min(abs(s_new - pos_at(p['wp'], now)), abs(s_new - p['s_seen']))
                if dm <= TRACK_MATCH_M:
                    cand.append((dm, fi, pid))
        cand.sort()
        used_f, used_p, assign = set(), set(), {}
        for dm, fi, pid in cand:
            if fi in used_f or pid in used_p:
                continue
            used_f.add(fi)
            used_p.add(pid)
            assign[fi] = pid
        new_tracks = {}
        trains = []
        for fi, f in enumerate(found):
            pid = assign.get(fi)
            if pid is None:
                pid = self.next_id
                self.next_id += 1
            last_s = self.net.tracks[f['tk']].st[f['chain'][-1][0]]['s']
            new_tracks[pid] = {'tk': f['tk'], 'wp': f['wp'], 'open_end': f['open_end'], 'seen': now,
                               'towards': f['towards'], 's_seen': pos_at(f['wp'], now), 'last_s': last_s}
            trains.append(self._export(pid, f['tk'], f['wp'], f['towards'], f, now, False))
        # ghosts: trains that ran past the last station with data (end of line / short turn)
        for pid, p in prev.items():
            if pid in used_p or not p['open_end'] or (lines is not None and p['tk'][0] not in lines):
                continue
            if now - p['seen'] > GHOST_MAX_AGE or p['wp'][-1][0] + 8 < now:
                continue
            if pos_at(p['wp'], now) < p['last_s'] - 1:
                continue    # it should still be visible at a station with data; it is not -> drop instead of guessing
            new_tracks[pid] = p
            trains.append(self._export(pid, p['tk'], p['wp'], p['towards'], None, now, True))
        self.tracks = new_tracks
        trains.sort(key=lambda t: (t['line'], t['dir'], t['s']))
        return trains

    def _export(self, pid, tk, wp, towards, f, now, ghost):
        track = self.net.tracks[tk]
        s = pos_at(wp, now)
        lat, lon = track.latlon(s)
        # previous / next station relative to s
        ss = [x['s'] for x in track.st]
        j = bisect.bisect_right(ss, s + 1.0)  # first station strictly ahead (1 m tolerance)
        nxt_i = min(j, track.n - 1)
        at_station = any(abs(s - x) < 1.0 for x in ss)
        prev_i = max(nxt_i - 1, 0)
        if at_station:
            k = min(range(track.n), key=lambda i: abs(ss[i] - s))
            prev_i = nxt_i = k
        next_dep = None
        if f:
            next_dep = {'t': f['head_t'], 'station': track.st[f['head_i']]['name']}
        return {
            'id': pid, 'line': tk[0], 'dir': tk[1], 'towards': towards or track.towards,
            'prev': track.st[prev_i]['name'], 'next': track.st[nxt_i]['name'],
            'at_station': at_station, 'ghost': ghost,
            's': round(s, 1), 'lat': round(lat, 6), 'lon': round(lon, 6),
            'bearing': round(track.bearing(s), 1),
            'next_departure': next_dep,
            'wp': [[round(t, 1), round(x, 1)] for t, x in wp if t <= now + HORIZON],
        }


# ---------------------------------------------------------------- meetings (cross-platform transfers)

def dwell_windows(wp, s_station, pad=8.0):
    """Time windows in which a train with waypoints wp stands in the station at arc length s_station."""
    out = []
    for (t0, s0), (t1, s1) in zip(wp, wp[1:]):
        if t1 > t0 and abs(s0 - s_station) < 1.0 and abs(s1 - s_station) < 1.0:
            out.append((t0 - pad, t1 + pad))
    return out


def meetings(net, trains, line_a, line_b, station):
    """For each direction: when do a train of line_a and one of line_b stand in `station` at the same time?
    Same direction on both lines = the direct cross-platform transfer."""
    result = {}
    for d in ('H', 'R'):
        ta, tb = net.tracks.get((line_a, d)), net.tracks.get((line_b, d))
        if not ta or not tb:
            continue
        sa = next((x['s'] for x in ta.st if x['name'] == station), None)
        sb = next((x['s'] for x in tb.st if x['name'] == station), None)
        if sa is None or sb is None:
            continue

        def wins(line, s):
            w = []
            for t in trains:
                if t['line'] == line and t['dir'] == d and not t['ghost']:
                    w += dwell_windows(t['wp'], s)
            return w
        found = []
        for a0, a1 in wins(line_a, sa):
            for b0, b1 in wins(line_b, sb):
                lo, hi = max(a0, b0), min(a1, b1)
                if lo < hi:
                    found.append([round(lo, 1), round(hi, 1)])
        found.sort()
        result[d] = {'a': ta.towards, 'b': tb.towards, 'meetings': found}
    return result
