"""Time Keeper integrated entry point. Python stdlib server; Flask-compatible WSGI app.
Run: python app_integrated_dhs_gpt_commited.py
No source file, model pickle or database is modified during startup.
"""
from __future__ import annotations
import json
import mimetypes
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote
from wsgiref.simple_server import make_server, WSGIRequestHandler, WSGIServer
from socketserver import ThreadingMixIn
from services.integrated_data_dhs_gpt_commited import DataStore, ROOT
from services.integrated_external_dhs_gpt_commited import External, now_kst
from services.integrated_recommendation_dhs_gpt_commited import recommend
from services.weather_delay_service_dhs_gpt_commited import get_weather_delay_summary

STATIC={'/':'Time_Keeper_INTEGRATED_dhs_gpt_commited.html',
        '/timekeeper':'Time_Keeper_INTEGRATED_dhs_gpt_commited.html',
        '/integrated_dhs_gpt_commited.js':'integrated_dhs_gpt_commited.js',
        '/integrated_dhs_gpt_commited.css':'integrated_dhs_gpt_commited.css'}

class Application:
    def __init__(self,root=ROOT,store_factory=DataStore,external=None):
        self.root=Path(root); self.store_factory=store_factory
        self.external=external or External(store_factory(root))

    def dispatch(self,method,path,query,payload):
        if method=='GET' and path in STATIC:
            file=self.root/STATIC[path]
            return 200,file.read_bytes(),mimetypes.guess_type(str(file))[0] or 'text/plain'
        if method=='GET' and path=='/api/catalog': return 200,self.store_factory(self.root).catalog,'application/json'
        if method=='GET' and path=='/api/health':
            store=self.store_factory(self.root)
            return 200,dict(status='ready',databases=store.health(),sources=store.workbooks.sources(),offline=os.getenv('BUSFLOW_OFFLINE')=='1',
                            key_configured=bool(os.getenv('DATA_API_KEY')),api_tested=False,
                            holiday_calendar_available=__import__('importlib.util',fromlist=['find_spec']).find_spec('holidays') is not None),'application/json'
        if method=='GET' and path=='/api/weather':
            try: target=datetime.fromisoformat(query.get('datetime',[now_kst().isoformat()])[0])
            except ValueError: raise ValueError('날씨 기준 시간을 확인해주세요.')
            regions={r:self.external.weather(target,r) for r in ('giheung','gangnam')}
            rain=regions['giheung'].get('rainfall_mm_per_hour')
            delay=get_weather_delay_summary(target.date(),rain) if rain is not None else None
            return 200,dict(regions=regions,delay=delay),'application/json'
        if method=='GET' and path.startswith('/api/realtime-final/'):
            # Refresh disk fallback on each explicit click, retain external request cache.
            self.external.store=self.store_factory(self.root)
            route=unquote(path.rsplit('/',1)[-1]); station=query.get('station_id',[''])[0]
            return 200,self.external.live(route,station),'application/json'
        if method=='POST' and path=='/api/recommend-final':
            if not isinstance(payload,dict): raise ValueError('잘못된 요청 형식')
            return 200,recommend(self.store_factory(self.root),self.external,payload),'application/json'
        return 404,{'error':'없는 경로입니다.'},'application/json'

    def __call__(self,environ,start_response):
        try:
            n=int(environ.get('CONTENT_LENGTH') or 0)
            if n>65536: raise ValueError('요청이 너무 큽니다.')
            payload=json.loads(environ['wsgi.input'].read(n)) if n else {}
            status,data,mime=self.dispatch(environ['REQUEST_METHOD'],environ['PATH_INFO'],parse_qs(environ.get('QUERY_STRING','')),payload)
        except (ValueError,TypeError,KeyError): status,data,mime=400,{'error':'요청 날짜·시간·정류장을 확인해주세요.'},'application/json'
        except Exception:
            status,data,mime=500,{'error':'서버 처리 중 오류가 발생했습니다. DB 구조와 실행 파일을 확인해주세요.'},'application/json'
        body=data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False,allow_nan=False).encode('utf-8')
        headers=[('Content-Type',mime+'; charset=utf-8'),('Content-Length',str(len(body))),('Cache-Control','no-store')]
        start_response(f'{status} '+{200:'OK',400:'Bad Request',404:'Not Found',500:'Internal Server Error'}[status],headers)
        return [body]

class Server(ThreadingMixIn,WSGIServer): daemon_threads=True
class QuietHandler(WSGIRequestHandler):
    def log_message(self,format,*args): pass

app=Application()
if __name__=='__main__':
    port=int(os.getenv('PORT','5001'))
    if '--open-browser' in sys.argv:
        import threading, webbrowser
        threading.Timer(1.0,lambda:webbrowser.open(f'http://127.0.0.1:{port}')).start()
    print(f'Time Keeper: http://127.0.0.1:{port}',flush=True)
    print('기존 data 폴더의 DB를 사용합니다. 종료: Ctrl+C',flush=True)
    with make_server('127.0.0.1',port,app,server_class=Server,handler_class=QuietHandler) as server:
        server.serve_forever()
