"""Timetable-only train positions (used for the S-Bahn, for which no open real-time feed exists).

A trip is [stop positions along the line (m), arrival, departure] per stop, times in seconds after midnight of the service
day (they may exceed 86400). For the current time the trips that are running are looked up and the position is interpolated
between the stops. Output has the same shape as the real-time engine, plus "schedule": true.
"""
import time
from datetime import date, timedelta

from engine import Engine, Network, HORIZON, pos_at  # noqa: F401  (pos_at re-exported for tests)


def vienna_offset(ts):
    """UTC offset of Europe/Vienna at epoch ts, by the EU rule (CEST from the last Sunday of March 01:00 UTC to the last Sunday
    of October 01:00 UTC) - no tz database needed (slim containers do not have one)."""
    y = time.gmtime(ts).tm_year

    def last_sunday(month):
        d = date(y, month, 31)
        return d - timedelta(days=(d.weekday() + 1) % 7)
    start = last_sunday(3)
    end = last_sunday(10)
    to_epoch = lambda d: (d - date(1970, 1, 1)).days * 86400 + 3600
    return 7200 if to_epoch(start) <= ts < to_epoch(end) else 3600


class ScheduleSource:
    def __init__(self, network_data, timetable):
        self.net = Network(network_data)
        self.engine = Engine(self.net)
        self.services = timetable['services']
        self.trips = timetable['trips']
        self.valid = (timetable['valid_from'], timetable['valid_to'])
        self.ids = {}            # (trip index, service date) -> stable id
        self.next_id = 1
        self.cache = (0.0, None)

    def lines(self):
        return self.net.tracks_lines()

    def active(self, svc, d, wd):
        s = self.services.get(svc)
        if not s:
            return False
        if d in s['rem']:
            return False
        if d in s['add']:
            return True
        return s['a'] <= d <= s['b'] and s['d'][wd] == '1'

    def trains(self, now=None):
        now = time.time() if now is None else now
        if self.cache[1] is not None and now - self.cache[0] < 3:
            return self.cache[1]
        off = vienna_offset(now)
        local = now + off
        today = date(1970, 1, 1) + timedelta(seconds=int(local // 86400) * 86400)
        sec_today = local - (local // 86400) * 86400
        out, seen = [], set()
        for k in (0, 1):                                    # the service day of today and of yesterday (times past midnight)
            day = today - timedelta(days=k)
            d = day.year * 10000 + day.month * 100 + day.day
            wd = day.weekday()
            tod = sec_today + 86400 * k
            for idx, t in enumerate(self.trips):
                p = t['p']
                if not (p[0][2] <= tod <= p[-1][1]) or not self.active(t['svc'], d, wd):
                    continue
                tr = self.build(idx, t, d, p, tod, now)
                if tr:
                    seen.add((idx, d))
                    out.append(tr)
        for key in [k for k in self.ids if k not in seen and len(self.ids) > 4000]:
            self.ids.pop(key, None)
        out.sort(key=lambda x: (x['line'], x['dir'], x['s']))
        self.cache = (now, out)
        return out

    def build(self, idx, t, d, p, tod, now):
        epoch = lambda tt: now + (tt - tod)
        wp, nxt = [], None
        # current position
        for i, (s, arr, dep) in enumerate(p):
            if arr <= tod <= dep:
                wp.append((now, s))
                nxt = i
                break
            if i + 1 < len(p) and dep < tod < p[i + 1][1]:
                s2, arr2, _ = p[i + 1]
                wp.append((now, s + (s2 - s) * (tod - dep) / max(arr2 - dep, 1)))
                nxt = i + 1
                break
        else:
            return None
        # what follows within the horizon
        for j in range(nxt, len(p)):
            s, arr, dep = p[j]
            if epoch(arr) > now + HORIZON:
                break
            if arr > tod:
                wp.append((epoch(arr), s))
            if dep > max(arr, tod) and epoch(dep) <= now + HORIZON:
                wp.append((epoch(dep), s))
        track = self.net.tracks[(t['l'], t['d'])]
        ss = [x['s'] for x in track.st]
        s_next = p[nxt][0]
        head_i = min(range(len(ss)), key=lambda i: abs(ss[i] - s_next))
        key = (idx, d)
        if key not in self.ids:
            self.ids[key] = self.next_id
            self.next_id += 1
        head_t = epoch(max(p[nxt][2], tod))
        f = {'head_t': head_t, 'head_i': head_i}
        tr = self.engine._export(self.ids[key], (t['l'], t['d']), wp, t['h'], f, now, False)
        tr['schedule'] = True
        tr['trip'] = t['n']
        return tr
