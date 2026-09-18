"""Synthetic fixtures only: these tests do not claim to validate the user's data/API."""
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from services.integrated_data_dhs_gpt_commited import DataStore, ROOT, GIHEUNG, SINNON
from services.integrated_recommendation_dhs_gpt_commited import recommend, adjusted_travel
from services.integrated_external_dhs_gpt_commited import External, request_json
from app_integrated_dhs_gpt_commited import Application

class Weather:
    def __init__(self,rain=3,available=True):self.rain=rain;self.available=available;self.calls=[]
    def weather(self,target,region):
        self.calls.append((target,region));return dict(available=self.available,rainfall_mm_per_hour=self.rain,region=region,label=region)

class IntegratedTests(unittest.TestCase):
    def setUp(self):
        calendar=patch('services.integrated_data_dhs_gpt_commited.holiday_kind', side_effect=lambda day: 'holiday' if day.weekday()>=5 else 'weekday')
        calendar.start();self.addCleanup(calendar.stop)
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'services').mkdir();(self.root/'data').mkdir()
        shutil.copy(ROOT/'config.py',self.root/'config.py')
        shutil.copy(ROOT/'services/stations_dhs_gpt_commited.json',self.root/'services/stations_dhs_gpt_commited.json')
        self.db=sqlite3.connect(self.root/'data/realtime.db')
        self.db.executescript('''CREATE TABLE realtime_location(route_name TEXT,veh_id TEXT,collected_at TEXT,station_id TEXT,station_seq INTEGER,remain_seat_cnt INTEGER);
        CREATE TABLE realtime_arrival_a(route_name TEXT,station_id TEXT,collected_at TEXT,veh_id_1 TEXT,predict_time_sec_1 INTEGER,remain_seat_cnt_1 INTEGER,veh_id_2 TEXT,predict_time_sec_2 INTEGER,remain_seat_cnt_2 INTEGER);
        CREATE TABLE travel_time_stats(route_name TEXT,departure_hour TEXT,p75_minutes REAL,sample_count INTEGER,updated_at TEXT);''')
        for day in ['2026-09-03','2026-09-10','2026-09-17']:
            for bus in range(3):
                start=datetime.fromisoformat(day+'T07:00')+timedelta(minutes=bus*12)
                for offset,sid,seq in [(0,'228000155',31),(10,GIHEUNG,39),(50,SINNON,49)]:
                    self.db.execute('INSERT INTO realtime_location VALUES (?,?,?,?,?,?)',('5003A',str(bus),(start+timedelta(minutes=offset)).isoformat(' '),sid,seq,20))
        self.db.commit()
        self.target=datetime(2026,9,24,7)
        self.payload=dict(date='2026-09-24',departure_time='07:00',arrival_time='09:00',commute_mode='morning',risk_mode='safe',
                          selected_boarding_stations=[{'route_name':'5003A','id':'4196091'}],destination='신논현역')
    def tearDown(self):self.db.close();self.temp.cleanup()
    def run_rec(self,**changes):return recommend(DataStore(self.root),Weather(),{**self.payload,**changes},now=datetime(2026,9,18))

    def test_weather_only_core(self):
        travel=DataStore(self.root).travel('5003A','228000155',SINNON,self.target)
        a=adjusted_travel(travel,'5003A',self.target,{'available':True,'rainfall_mm_per_hour':3})
        self.assertAlmostEqual(a['minutes'],10+40*a['weight']);self.assertEqual(a['pre_minutes'],10)
        self.assertNotAlmostEqual(a['minutes'],50*a['weight'])
    def test_weather_no_rain_and_evening(self):
        t=dict(pre=10,core=40,after=5,scope=True)
        self.assertEqual(adjusted_travel(t,'5003A',self.target,dict(available=True,rainfall_mm_per_hour=0))['minutes'],55)
        self.assertEqual(adjusted_travel(t,'5003B',self.target,dict(available=True,rainfall_mm_per_hour=10))['minutes'],55)
        self.assertEqual(adjusted_travel(t,'5003A',self.target.replace(hour=10),dict(available=True,rainfall_mm_per_hour=10))['minutes'],55)
    def test_weather_uses_entry_hour(self):
        t=dict(pre=20,core=40,after=0,scope=True)
        result=adjusted_travel(t,'5003A',self.target.replace(hour=8,minute=50),dict(available=True,rainfall_mm_per_hour=10))
        self.assertFalse(result['weather_applied'])
    def test_missing_weather_not_reported_as_dry(self):
        result=adjusted_travel(dict(pre=0,core=40,after=0,scope=True),'5003A',self.target,dict(available=False))
        self.assertFalse(result['weather_applied']);self.assertTrue(result['scope_supported'])
    def test_headway_existing_passages(self):
        p=DataStore(self.root).profile('5003A','228000155',self.target)
        self.assertEqual(p['headway_minutes'],12);self.assertEqual(p['days'],3);self.assertEqual(p['seat_samples'],9)
    def test_future_ignores_same_day_records(self):
        self.db.execute('INSERT INTO realtime_location VALUES (?,?,?,?,?,?)',('5003A','future','2026-09-24 07:00:00','228000155',31,0));self.db.commit()
        self.assertEqual(DataStore(self.root).profile('5003A','228000155',self.target)['seat_samples'],9)
    def test_repeat_runs_not_cross_matched(self):
        d=DataStore(self.root);self.assertEqual(len(d.segment_samples('5003A','228000155',SINNON)),9)
        self.assertTrue(all(x['total']==50 for x in d.segment_samples('5003A','228000155',SINNON)))
    def test_arrival_forecasts_deduplicate_vehicle(self):
        for n in range(3):
            for minute in [0,1,2]:
                collected=datetime(2026,9,17,7)+timedelta(minutes=n*12+minute)
                self.db.execute('INSERT INTO realtime_arrival_a VALUES (?,?,?,?,?,?,?,?,?)',('5001A',GIHEUNG,collected.isoformat(' '),f'v{n}',(3-minute)*60,10,None,None,None))
        self.db.commit();p=DataStore(self.root).profile('5001A',GIHEUNG,self.target)
        self.assertEqual(p['seat_samples'],3);self.assertEqual(p['headway_samples'],2);self.assertEqual(p['headway_minutes'],12)
    def test_feasible_candidates_and_original_rank(self):
        r=self.run_rec();self.assertEqual(r['status'],'ok');self.assertEqual(r['candidates'][0]['rank'],1)
        self.assertTrue(all(c['deadline_met'] for c in r['candidates']))
        self.assertTrue(all(c['segment_minutes']['local_before']==10 for c in r['candidates']))
    def test_deadline_rejects_all_late_candidates(self):
        r=self.run_rec(arrival_time='07:20');self.assertFalse(r['candidates']);self.assertEqual(r['status'],'no_feasible_route')
    def test_unknown_destination_is_insufficient_not_impossible(self):
        r=self.run_rec(destination='양재역');self.assertFalse(r['candidates']);self.assertEqual(r['status'],'insufficient_data')
    def test_missing_database_is_not_created(self):
        store=DataStore(self.root);self.assertEqual(store.read('busflow_5003.db','congestion'),[])
        self.assertFalse((self.root/'data/busflow_5003.db').exists())
    def test_tampered_station_rejected(self):
        with self.assertRaises(ValueError):self.run_rec(selected_boarding_stations=[dict(route_name='5003A',id='fake')])
    def test_fast_mode_uses_final_arrival(self):
        r=self.run_rec(risk_mode='fast');times=[c['arrival_datetime'] for c in r['candidates']]
        self.assertEqual(times,sorted(times))
    def test_offline_blocks_external_requests(self):
        with patch.dict(os.environ,{'BUSFLOW_OFFLINE':'1'}):
            with self.assertRaises(RuntimeError): request_json('https://example.com',{})
    def test_wsgi_recommend_and_weather(self):
        app=Application(self.root,external=Weather());statuses=[]
        body=json.dumps(self.payload).encode()
        with patch('services.integrated_recommendation_dhs_gpt_commited.now_kst',return_value=datetime(2026,9,18)):
            result=app(dict(REQUEST_METHOD='POST',PATH_INFO='/api/recommend-final',QUERY_STRING='',CONTENT_LENGTH=str(len(body)),**{'wsgi.input':io.BytesIO(body)}),lambda status,headers:statuses.append(status))
        self.assertEqual(statuses,['200 OK']);self.assertEqual(json.loads(b''.join(result))['status'],'ok')
        status,data,_=app.dispatch('GET','/api/weather',{'datetime':['2026-09-24T07:00']},{})
        self.assertEqual(set(data['regions']),{'giheung','gangnam'})
    def test_live_adapter_a_and_b_mock_responses(self):
        for route,station in [('5003A',GIHEUNG),('5001B','121000004')]:
            ext=External(DataStore(self.root))
            response={'response':{'msgHeader':{'resultCode':0},'msgBody':{'busArrivalList':[{
                'routeId':ext.store.catalog[route]['route_id'],'staOrder':27,
                'predictTimeSec1':180,'predictTimeSec2':900,'remainSeatCnt1':-1,'remainSeatCnt2':0}]}}}
            with patch.dict(os.environ,{'DATA_API_KEY':'test-placeholder'}),patch('services.integrated_external_dhs_gpt_commited.request_json',return_value=response):
                r=ext.live(route,station)
            self.assertFalse(r['stale']);self.assertEqual(r['headway_minutes'],12)
            self.assertIsNone(r['buses'][0]['remain_seats']);self.assertEqual(r['buses'][1]['remain_seats'],0)

    def test_forecast_uses_selected_hour_accumulation(self):
        ext=External(DataStore(self.root))
        response={'hourly':{'time':['2026-09-24T07:00','2026-09-24T08:00'],
                  'temperature_2m':[20,21],'rain':[99,2],'showers':[0,1],'weather_code':[61,61]}}
        with patch('services.integrated_external_dhs_gpt_commited.now_kst',return_value=datetime(2026,9,24,6)),patch('services.integrated_external_dhs_gpt_commited.request_json',return_value=response):
            r=ext.weather(datetime(2026,9,24,7,30),'giheung')
        self.assertEqual(r['rainfall_mm_per_hour'],3);self.assertEqual(r['temperature'],20)
        self.assertEqual(r['target_time'],'2026-09-24T07:00')

    def test_negative_missing_seat_not_zero(self):
        from services.integrated_data_dhs_gpt_commited import number
        self.assertIsNone(number(-1));self.assertEqual(number(0),0)

if __name__=='__main__': unittest.main()
