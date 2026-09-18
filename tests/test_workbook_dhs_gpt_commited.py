"""Regression checks against real, versioned repository spreadsheets; no API calls."""
import shutil, sqlite3, tempfile, unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from services.integrated_data_dhs_gpt_commited import ROOT, DataStore, GIHEUNG, SINNON
from services.workbook_data_dhs_gpt_commited import WorkbookData, sheet, HEADWAY, CONGESTION, DATED, DROPS
from services.integrated_recommendation_dhs_gpt_commited import recommend
from app_integrated_dhs_gpt_commited import Application

class NoNetwork:
    def weather(self,*args): return {'available':False}

class WorkbookTests(unittest.TestCase):
    def setUp(self):
        calendar=patch('services.integrated_data_dhs_gpt_commited.holiday_kind', side_effect=lambda day:'holiday' if day.weekday()>=5 else 'weekday')
        calendar.start();self.addCleanup(calendar.stop)
        self.store=DataStore();self.target=datetime(2026,9,21,7)
    def test_actual_row_counts(self):
        self.assertEqual(len(sheet(ROOT,HEADWAY,'headway')),239)
        self.assertEqual(len(sheet(ROOT,CONGESTION,'전체_상세')),1437)
        self.assertEqual(len(sheet(ROOT,DROPS,'raw_drops')),531)
    def test_5003_wide_sheet_is_normalized_without_duplicate_pivots(self):
        self.assertEqual(sum(len(v) for v in self.store.workbooks.dated().values()),59709)
        rows=next(v for (r,s),v in self.store.workbooks.dated().items() if r=='5003B')
        self.assertTrue(any(r['time'].hour==0 for r in rows))
        self.assertTrue(all(0<=r['time'].hour<24 for r in rows))
    def test_weighted_headway_and_missing_full_rate(self):
        p=self.store.profile('5001A',GIHEUNG,self.target)
        self.assertAlmostEqual(p['headway_minutes'],8.895530726256984)
        self.assertEqual(p['headway_samples'],179)
        self.assertIsNone(p['full_rate']);self.assertIsNone(p['headway_p75']);self.assertIsNone(p['seat_median'])
    def test_dated_congestion_uses_selected_day_season_and_hour(self):
        stop=self.store.catalog['5003A']['boarding'][-1]
        c=self.store.congestion('5003A',stop['historical_id'],self.target)
        self.assertEqual(c['value'],86.7);self.assertEqual(c['sample_count'],11)
        self.assertEqual(c['source'],DATED)
    def test_summary_is_not_used_before_workbook_existed(self):
        p=self.store.workbooks.headway('5001A','기흥역',datetime(2026,8,1,7),lambda s:s)
        self.assertIsNone(p)
    def test_local_sections_do_not_claim_core_travel(self):
        rows=self.store.workbooks.sections('5001B',datetime(2026,9,21,20))
        self.assertTrue(rows)
        self.assertTrue(all('기흥' not in r['section'] for r in rows))
        self.assertIsNone(self.store.travel('5001A',GIHEUNG,SINNON,self.target))
    def test_actual_sources_wsgi_response_without_travel_fabrication(self):
        stop=self.store.catalog['5003A']['boarding'][-1]
        payload=dict(date='2026-09-21',departure_time='07:00',arrival_time='09:00',commute_mode='morning',selected_boarding_stations=[dict(route_name='5003A',id=stop['id'])],destination='신논현역')
        result=recommend(self.store,NoNetwork(),payload,now=datetime(2026,9,18))
        self.assertEqual(result['status'],'insufficient_data');self.assertEqual(result['candidates'],[])
        self.assertEqual(result['station_evidence'][0]['congestion']['value'],86.7)
        self.assertGreater(result['station_evidence'][0]['profile']['headway_minutes'],0)
    def test_summary_profile_works_when_real_core_record_is_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir();(root/'analysis').mkdir();(root/'services').mkdir()
            for f in ['config.py','services/stations_dhs_gpt_commited.json',HEADWAY]:shutil.copy(ROOT/f,root/f)
            with sqlite3.connect(root/'data/realtime.db') as db:
                db.execute('CREATE TABLE travel_time_stats(route_name TEXT,departure_hour INTEGER,p75_minutes REAL,sample_count INTEGER,updated_at TEXT)')
                db.execute("INSERT INTO travel_time_stats VALUES ('5001A',7,40,20,'2026-09-16')")
            store=DataStore(root);stop=store.catalog['5001A']['boarding'][-1]
            payload=dict(date='2026-09-21',departure_time='07:00',arrival_time='08:00',commute_mode='morning',selected_boarding_stations=[dict(route_name='5001A',id=stop['id'])],destination='신논현역')
            result=recommend(store,NoNetwork(),payload,now=datetime(2026,9,18)); c=result['candidates'][0]
            self.assertFalse(c['full_rate_known']);self.assertEqual(c['seat_statistic'],'평균');self.assertEqual(c['stability_grade'],'주의')
            self.assertEqual(c['travel_time_minutes'],40)

if __name__=='__main__': unittest.main()
