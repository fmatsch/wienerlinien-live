"""Streams the big GTFS files and keeps only U-Bahn data -> data/raw/gtfs_u.json"""
import csv, json, sys
from collections import defaultdict, Counter
G='data/gtfs/'
def rd(f):
    return csv.DictReader(open(G+f,encoding='utf-8-sig'))
routes={r['route_id']:r['route_short_name'] for r in rd('routes.txt') if r['route_id'].startswith('21-U')}
trips={}
for t in rd('trips.txt'):
    if t['route_id'] in routes:
        trips[t['trip_id']]=(routes[t['route_id']],t['direction_id'],t['shape_id'],t['trip_headsign'],t['service_id'])
print(len(trips),'trips',file=sys.stderr)
def sec(s):
    h,m,x=s.split(':'); return int(h)*3600+int(m)*60+int(x)
st=defaultdict(list)
for r in rd('stop_times.txt'):
    if r['trip_id'] in trips:
        st[r['trip_id']].append((int(r['stop_sequence']),r['stop_id'],sec(r['arrival_time']),sec(r['departure_time'])))
print(len(st),'trips with times',file=sys.stderr)
shapes_needed={v[2] for v in trips.values()}
sh=defaultdict(list)
for r in rd('shapes.txt'):
    if r['shape_id'] in shapes_needed:
        sh[r['shape_id']].append((int(r['shape_pt_sequence']),float(r['shape_pt_lat']),float(r['shape_pt_lon'])))
stops={r['stop_id']:(r['stop_name'],float(r['stop_lat']),float(r['stop_lon'])) for r in rd('stops.txt') if r['stop_id'].startswith('at:49:')}
cal={r['service_id']:r for r in rd('calendar.txt')}
out={'trips':{k:{'line':v[0],'dir':v[1],'shape':v[2],'head':v[3],'service':v[4],'st':sorted(st[k])} for k,v in trips.items() if k in st},
     'shapes':{k:[(a,b) for _,a,b in sorted(v)] for k,v in sh.items()},'stops':stops,
     'cal':{k:cal[k] for k in {v[4] for v in trips.values()} if k in cal}}
json.dump(out,open('data/raw/gtfs_u.json','w'))
