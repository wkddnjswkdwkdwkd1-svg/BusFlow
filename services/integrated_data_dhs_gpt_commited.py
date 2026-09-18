"""Read existing BusFlow data without modifying source files or databases.
All timestamps are naive Asia/Seoul local time, matching the existing collectors.
"""
from __future__ import annotations
import ast
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from services.workbook_data_dhs_gpt_commited import WorkbookData

ROOT = Path(__file__).resolve().parents[1]
HIST_ROUTES = {'5001A':'41006433','5001B':'41006248','5003A':'41006409','5003B':'41006064'}
GIHEUNG = '228000682'
SINNON = '228001278'


def literal_config(root=ROOT):
    values = {}
    for node in ast.parse((Path(root)/'config.py').read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name): values[target.id] = value
    return values


def canon(name):
    name = str(name or '').replace('컨트리크럽','컨트리클럽')
    for key in ['신논현','강남대','강남역','양재역','기흥역','뱅뱅','우성','교육개발원','매헌','말죽거리']:
        if key in name: return key
    return name.replace(' ', '').replace('(중)', '')


def catalog(root=ROOT):
    c = literal_config(root)
    seed = json.loads((Path(root)/'services/stations_dhs_gpt_commited.json').read_text(encoding='utf-8'))
    result = {}
    for route in HIST_ROUTES:
        family = route[:4]
        if route.endswith('A'):
            historical = seed[family]['yongin']
            realtime = c['REALTIME_STATIONS_'+route]
            rows = [dict(id=str(h[0]), name=h[1], historical_id=str(h[0]), realtime_id=str(r['id']), seq=h[2])
                    for h,r in zip(historical,realtime)]
            dest = [dict(id=str(h[0]),name=h[1],historical_id=str(h[0]),
                         realtime_id=SINNON if '신논현' in h[1] else None,seq=None)
                    for h in seed[family]['seoul']]
        else:
            history = {canon(h[1]):str(h[0]) for h in seed[family]['seoul']}
            rows = [dict(id=str(r['id']),name=r['name'],realtime_id=str(r['id']),
                         historical_id=history.get(canon(r['name'])),seq=None)
                    for r in c['REALTIME_STATIONS_B']]
            # Opposite-side stop IDs cannot be copied from A. Resolve by observed B data.
            dest = [dict(id='destination:'+str(h[0]),name=h[1],historical_id=None,realtime_id=None,seq=None)
                    for h in seed[family]['yongin']]
        result[route] = dict(route=route,route_id=c['REALTIME_ROUTES'][route],
                             direction='to_seoul' if route.endswith('A') else 'to_yongin',
                             variant=route[-1],boarding=rows,destinations=dest)
    return result


def dt(value):
    if isinstance(value, datetime): return value
    text = str(value)
    return datetime.strptime(text[:8], '%Y%m%d') if len(text)==8 else datetime.fromisoformat(text)


def number(value):
    try:
        n=float(value)
        return n if math.isfinite(n) and n>=0 else None
    except (TypeError,ValueError): return None


def q75(values):
    values=sorted(values)
    if not values: return None
    pos=(len(values)-1)*.75; low=int(pos); high=min(low+1,len(values)-1)
    return values[low]+(values[high]-values[low])*(pos-low)


def holiday_kind(day):
    try:
        import holidays
        return 'holiday' if day.weekday()>=5 or day in holidays.country_holidays('KR',years=[day.year]) else 'weekday'
    except ImportError:
        return 'holiday' if day.weekday()>=5 else 'weekday'


def seasonal(month):
    return 'winter' if month in (12,1,2) else 'spring' if month in (3,4,5) else 'summer' if month in (6,7,8) else 'fall'


class DataStore:
    def __init__(self, root=ROOT):
        self.root=Path(root); self.catalog=catalog(root); self._rows={}; self._runs={}; self._profiles={}; self._trips={}; self._congestions={}; self._location_cache={}
        self.config=literal_config(root)
        self.workbooks=WorkbookData(root)

    def read(self, filename, table):
        key=(filename,table)
        if key in self._rows: return self._rows[key]
        path=self.root/'data'/filename
        if not path.is_file(): self._rows[key]=[]; return []
        try:
            with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True) as conn:
                conn.row_factory=sqlite3.Row
                rows=[dict(r) for r in conn.execute('SELECT * FROM "'+table+'"')]
        except sqlite3.Error: rows=[]
        self._rows[key]=rows
        return rows

    def health(self):
        return {name:dict(exists=(self.root/'data'/name).is_file()) for name in ['busflow.db','busflow_5003.db','realtime.db']}

    def locations(self,route):
        if route not in self._location_cache:
            self._location_cache[route]=[r for r in self.read('realtime.db','realtime_location') if r.get('route_name')==route]
        return self._location_cache[route]

    def resolve_destination(self,route,dest):
        # Only accept a destination present in our server-side catalog.
        matches=[s for s in self.catalog[route]['destinations'] if str(s['id'])==str(dest) or canon(s['name'])==canon(dest)]
        if not matches: return None
        s=dict(matches[0])
        if s.get('realtime_id'): return s
        ids=set()
        names=self.config.get('REALTIME_STATION_NAMES',{})
        for row in self.locations(route):
            rid=str(row.get('station_id',''))
            name=row.get('station_name') or names.get(rid,'')
            if name and canon(name)==canon(s['name']): ids.add(rid)
        if len(ids)==1: s['realtime_id']=ids.pop(); return s
        return None

    def runs(self,route):
        if route in self._runs: return self._runs[route]
        groups=defaultdict(list)
        for row in self.locations(route):
            try:
                t=dt(row['collected_at']); seq=int(row['station_seq']); vehicle=str(row['veh_id'])
            except (KeyError,ValueError,TypeError): continue
            if not vehicle or vehicle=='None': continue
            groups[vehicle].append(dict(time=t,seq=seq,id=str(row['station_id']),seats=number(row.get('remain_seat_cnt'))))
        runs=[]
        for vehicle, points in groups.items():
            current=[]; prev=None
            for point in sorted(points,key=lambda r:r['time']):
                if prev and (point['time'].date()!=prev['time'].date() or point['seq']<prev['seq'] or (point['time']-prev['time']).total_seconds()>7200):
                    if current: runs.append(current)
                    current=[]
                if current and current[-1]['id']==point['id']:
                    current[-1]['last']=point['time']
                else: current.append(dict(point,last=point['time'],vehicle=vehicle))
                prev=point
            if current: runs.append(current)
        self._runs[route]=runs
        return runs

    @staticmethod
    def comparable(samples,target,time_key='time'):
        past=[s for s in samples if s[time_key].date()<target.date() and s[time_key].hour==target.hour]
        same_kind=[s for s in past if holiday_kind(s[time_key].date())==holiday_kind(target.date())]
        exact=[s for s in same_kind if s[time_key].weekday()==target.weekday()]
        season=[s for s in exact if seasonal(s[time_key].month)==seasonal(target.month)]
        for rows,label in [(season,'동일 계절·요일·시간'),(exact,'동일 요일·시간'),(same_kind,'동일 평일/휴일·시간')]:
            if rows: return rows,label
        return [],'해당 시간대 기록 없음'

    def segment_samples(self,route,start,end):
        key=(route,start,end)
        if key in self._trips: return self._trips[key]
        samples=[]
        for run in self.runs(route):
            starts=[i for i,p in enumerate(run) if p['id']==start]
            ends=[i for i,p in enumerate(run) if p['id']==end]
            if len(starts)!=1 or len(ends)!=1 or starts[0]>=ends[0]: continue
            a,b=starts[0],ends[0]
            begin=run[a]['last']; finish=run[b]['time']; total=(finish-begin).total_seconds()/60
            if not 0<total<=180: continue
            pre=total; core=0.; after=0.; scope=False; core_hour=None
            if route.endswith('A'):
                gi=next((i for i,p in enumerate(run) if p['id']==GIHEUNG),None)
                si=next((i for i,p in enumerate(run) if p['id']==SINNON),None)
                if gi is not None and si is not None and gi<si:
                    lo=max(a,gi); hi=min(b,si)
                    if lo<hi:
                        # Additive partition; no second insertion of the highway's full time.
                        left=begin if lo==a else run[lo]['time']
                        right=finish if hi==b else run[hi]['time']
                        pre=(left-begin).total_seconds()/60
                        core=(right-left).total_seconds()/60
                        after=total-pre-core
                        core_hour=(left-begin).total_seconds()/60
                        scope=core>0
            samples.append(dict(time=begin,total=total,pre=pre,core=core,after=after,scope=scope,core_offset=core_hour or 0))
        self._trips[key]=samples
        return samples

    def travel(self,route,start,end,target):
        samples,label=self.comparable(self.segment_samples(route,start,end),target)
        if samples:
            # Use one observed trip at the p75 rank to preserve additive segment partition.
            chosen=sorted(samples,key=lambda x:x['total'])[math.ceil((len(samples)-1)*.75)]
            return {**chosen,'sample_count':len(samples),'basis':label,'source':'observed_vehicle_trips'}
        # Legacy statistics are valid only for their explicitly defined core segment.
        if route.endswith('A') and start==GIHEUNG and end==SINNON:
            for r in self.read('realtime.db','travel_time_stats'):
                if r.get('route_name')!=route or int(r.get('departure_hour',-1))!=target.hour: continue
                try:
                    if dt(r['updated_at']).date()>=target.date(): continue
                except (KeyError,ValueError,TypeError): continue
                total=number(r.get('p75_minutes')); count=number(r.get('sample_count'))
                if total is not None and total>0 and count:
                    return dict(total=total,pre=0.,core=total,after=0.,scope=True,core_offset=0,
                                sample_count=int(count),basis='기존 노선·시간대 p75 (요일 미분리)',source='legacy_core_stats')
        return None

    def profile(self,route,station,target):
        key=(route,station,target.date(),target.hour)
        if key in self._profiles: return self._profiles[key]
        # First prefer observed station passages; otherwise deduplicate arrival predictions.
        passages=[]
        for run in self.runs(route):
            for p in run:
                if p['id']==station:
                    passages.append(dict(time=p['time'],vehicle=p['vehicle'],seats=p['seats'],estimated=False))
        if not passages:
            groups=defaultdict(list)
            for table in ['realtime_arrival_a','realtime_arrival_b','realtime_arrival']:
                for r in self.read('realtime.db',table):
                    if r.get('route_name')!=route or str(r.get('station_id'))!=station: continue
                    try: collected=dt(r['collected_at'])
                    except (KeyError,ValueError,TypeError): continue
                    if collected.date()>=target.date(): continue
                    for n in [1,2]:
                        vid=r.get(f'veh_id_{n}'); eta=number(r.get(f'predict_time_sec_{n}')); seats=number(r.get(f'remain_seat_cnt_{n}'))
                        if vid and eta is not None and eta<=7200:
                            groups[(collected.date(),str(vid))].append((collected,eta,seats))
            for (_,vehicle), rows in groups.items():
                episodes=[]; current=[]; prev=None
                for row in sorted(set(rows),key=lambda p:p[0]):
                    arrival=row[0]+timedelta(seconds=row[1])
                    if prev and (row[0]-prev[0]>timedelta(minutes=20) or abs((arrival-(prev[0]+timedelta(seconds=prev[1]))).total_seconds())>1800):
                        episodes.append(current); current=[]
                    current.append(row); prev=row
                if current: episodes.append(current)
                for episode in episodes:
                    closest=min(episode,key=lambda p:p[1])
                    passages.append(dict(time=closest[0]+timedelta(seconds=closest[1]),vehicle=vehicle,seats=closest[2],estimated=True))
        selected,label=self.comparable(passages,target)
        by_day=defaultdict(list)
        for p in selected: by_day[p['time'].date()].append(p)
        intervals=[]
        for day,rows in by_day.items():
            rows=sorted(rows,key=lambda p:p['time'])
            for a,b in zip(rows,rows[1:]):
                gap=(b['time']-a['time']).total_seconds()/60
                if a['vehicle']!=b['vehicle'] and 1<=gap<=120: intervals.append(gap)
        seats=[p['seats'] for p in selected if p['seats'] is not None]
        result=None
        if intervals and seats:
            result=dict(headway_minutes=median(intervals),headway_p75=q75(intervals),seat_median=median(seats),
                        full_rate=sum(s==0 for s in seats)/len(seats),seat_samples=len(seats),
                        headway_samples=len(intervals),days=len(by_day),basis=label,
                        source='historical_arrival_estimates' if any(p['estimated'] for p in selected) else 'historical_station_passages')
        if result is None:
            stop=next((s for s in self.catalog[route]['boarding'] if s['realtime_id']==station),None)
            if stop: result=self.workbooks.headway(route,stop['name'],target,canon)
        self._profiles[key]=result
        return result

    def congestion(self,route,historical_id,target):
        if not historical_id: return None
        key=(route,historical_id,target.date(),target.hour)
        if key in self._congestions: return self._congestions[key]
        file='busflow_5003.db' if route.startswith('5003') else 'busflow.db'
        samples=[]
        for r in self.read(file,'congestion'):
            if str(r.get('route_id'))!=HIST_ROUTES[route] or str(r.get('station_id'))!=str(historical_id): continue
            try:
                hour=int(str(r['time_zone'])[:2]); t=dt(r['opr_ymd'])+timedelta(hours=hour)
            except (ValueError,TypeError,KeyError): continue
            n=number(r.get('congestion'))
            if n is not None: samples.append(dict(time=t,value=n))
        rows,label=self.comparable(samples,target)
        result=dict(value=round(sum(r['value'] for r in rows)/len(rows),1),sample_count=len(rows),basis=label,source='data/'+file+' / congestion') if rows else None
        if result is None: result=self.workbooks.congestion(route,historical_id,target,self.comparable)
        self._congestions[key]=result
        return result
