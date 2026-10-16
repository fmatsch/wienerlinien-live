import csv, json, urllib.request, sys, time
ids=[int(r['StopID']) for r in csv.DictReader(open('data/raw/wienerlinien-ogd-haltepunkte.csv'),delimiter=';') if 4000<=int(r['StopID'])<5000]
H={'Accept':'application/json','User-Agent':'fmatsch-ubahn-livemap/0.1 (+https://fmatsch.ist/wienerlinien)'}
out={}
n=int(sys.argv[1]) if len(sys.argv)>1 else 60
for i in range(0,len(ids),n):
    chunk=ids[i:i+n]
    url='https://www.wienerlinien.at/ogd_realtime/monitor?'+'&'.join(f'stopId={x}' for x in chunk)
    d=json.load(urllib.request.urlopen(urllib.request.Request(url,headers=H),timeout=30))
    print(len(url),d['message']['value'],len(d['data']['monitors']) if d.get('data') else d['message'])
    for m in (d.get('data') or {}).get('monitors',[]):
        rbl=m['locationStop']['properties']['attributes']['rbl']
        for l in m['lines']:
            if l['name'].startswith('U'):
                out.setdefault(rbl,[]).append({'line':l['name'],'dir':l['direction'],'towards':l['towards'],'title':m['locationStop']['properties']['title'],'diva':m['locationStop']['properties']['name'],'gate':m['locationStop']['properties'].get('gate'),'coord':m['locationStop']['geometry']['coordinates'],'n':len(l['departures'].get('departure',[]))})
    time.sleep(1)
json.dump(out,open('data/raw/ubahn_rbls.json','w'),ensure_ascii=False,indent=1)
print(len(out))
