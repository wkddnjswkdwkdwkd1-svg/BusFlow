"""External requests are confined here; BUSFLOW_OFFLINE=1 disables all of them."""
from __future__ import annotations
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, unquote
from urllib.request import urlopen
from services.integrated_data_dhs_gpt_commited import ROOT, number

KST=timezone(timedelta(hours=9))
def now_kst(): return datetime.now(KST).replace(tzinfo=None)


def load_env(root=ROOT):
    path=Path(root)/'.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            key,sep,value=line.strip().partition('=')
            if sep and key and not key.startswith('#'):
                os.environ.setdefault(key.strip(),value.strip().strip('"').strip("'"))


def request_json(url,params):
    if os.getenv('BUSFLOW_OFFLINE')=='1': raise RuntimeError('외부 API 검증 보류: 오프라인 모드')
    try:
        with urlopen(url+'?'+urlencode(params),timeout=12) as response:
            return json.load(response)
    except Exception:
        # Do not expose request URLs or service keys in exceptions/logs.
        raise RuntimeError('외부 서비스 연결 실패. 잠시 후 다시 조회해주세요.') from None


class External:
    REGIONS={'giheung':('기흥구',37.2803,127.1146),'gangnam':('강남구',37.5172,127.0473)}
    def __init__(self,store):
        self.store=store; self.cache={}; load_env(store.root)

    def weather(self,target,region='giheung'):
        if region not in self.REGIONS: raise ValueError('지원하지 않는 날씨 지역')
        now=now_kst(); day_delta=(target.date()-now.date()).days
        if not 0<=day_delta<=6:
            return dict(region=region,label=self.REGIONS[region][0],available=False,
                        message='예보 지원 범위는 오늘부터 6일 뒤까지입니다.',target_time=target.isoformat())
        key=('weather',region)
        cached=self.cache.get(key)
        try:
            failed=self.cache.get(('weather_failure',region))
            if failed and time.monotonic()-failed<60: raise RuntimeError('날씨 서비스 재시도 대기')
            if cached and time.monotonic()-cached[0]<600: data=cached[1]
            else:
                label,lat,lon=self.REGIONS[region]
                data=request_json('https://api.open-meteo.com/v1/forecast',dict(latitude=lat,longitude=lon,
                    hourly='temperature_2m,rain,showers,weather_code',timezone='Asia/Seoul',forecast_days=7))
                self.cache[key]=(time.monotonic(),data)
            hourly=data['hourly']; stamp=target.replace(minute=0,second=0,microsecond=0).isoformat(timespec='minutes')
            i=hourly['time'].index(stamp)
            # Rain/showers are preceding-hour sums. The next timestamp covers
            # the selected hour (07:00 -> use accumulation ending at 08:00).
            rain_index=i+1
            large_scale=number(hourly['rain'][rain_index]); showers=number(hourly['showers'][rain_index])
            if large_scale is None or showers is None: raise ValueError('missing rainfall')
            rain=large_scale+showers; temp=hourly['temperature_2m'][i]
            code=int(hourly['weather_code'][i]); condition='맑음' if code==0 else '구름' if code in (1,2,3) else '안개' if code in (45,48) else '눈' if code in (71,73,75,77,85,86) else '비'
            return dict(region=region,label=self.REGIONS[region][0],available=True,temperature=temp,
                        rainfall_mm_per_hour=rain,condition=condition,source='Open-Meteo 시간별 예보',
                        target_time=stamp,representative_point=True,
                        retrieved_at=(now-timedelta(seconds=time.monotonic()-self.cache[key][0])).isoformat(timespec='seconds'))
        except (RuntimeError,KeyError,ValueError,IndexError,TypeError):
            self.cache[('weather_failure',region)]=time.monotonic()
            return dict(region=region,label=self.REGIONS[region][0],available=False,
                        message='날씨 정보를 불러오지 못했습니다.',target_time=target.isoformat())

    def live(self,route,station_id):
        spec=self.store.catalog.get(route)
        if not spec or station_id not in {s['realtime_id'] for s in spec['boarding']}:
            raise ValueError('지원하지 않는 노선·정류장 조합')
        key=('live',station_id); cached=self.cache.get(key); age=None
        try:
            if cached and time.monotonic()-cached[0]<60:
                arrivals=cached[1]; age=time.monotonic()-cached[0]
            else:
                token=os.getenv('DATA_API_KEY','').strip()
                if not token: raise RuntimeError('DATA_API_KEY 환경변수가 필요합니다.')
                data=request_json('https://apis.data.go.kr/6410000/busarrivalservice/v2/getBusArrivalListv2',
                                  dict(serviceKey=unquote(token),stationId=station_id,format='json'))
                response=data['response']; header=response['msgHeader']
                if str(header.get('resultCode')) not in ('0','4'): raise RuntimeError('버스 API 응답 오류')
                arrivals=(response.get('msgBody') or {}).get('busArrivalList') or []
                if isinstance(arrivals,dict): arrivals=[arrivals]
                self.cache[key]=(time.monotonic(),arrivals); age=0
            targets=[r for r in arrivals if str(r.get('routeId'))==str(spec['route_id'])]
            # Multiple occurrences cannot be silently resolved by taking the first result.
            orders={str(r.get('staOrder')) for r in targets}
            if len(orders)>1: raise RuntimeError('같은 정류장의 운행 방향을 추가 확인해야 합니다.')
            bus=targets[0] if targets else {}
            result=dict(route=route,station_id=station_id,stale=False,source='실시간 API' if age==0 else '최근 API 캐시',
                        collected_at=(now_kst()-timedelta(seconds=age)).isoformat(timespec='seconds'),age_seconds=int(age),buses=[])
            for n in (1,2):
                eta=number(bus.get(f'predictTimeSec{n}'))
                if eta is None:
                    minutes=number(bus.get(f'predictTime{n}')); eta=minutes*60 if minutes is not None else None
                if eta is not None:
                    result['buses'].append(dict(arrival_seconds=max(0,eta-age),remain_seats=number(bus.get(f'remainSeatCnt{n}')),vehicle_id=bus.get(f'vehId{n}')))
            result['headway_minutes']=(result['buses'][1]['arrival_seconds']-result['buses'][0]['arrival_seconds'])/60 if len(result['buses'])==2 else None
            return result
        except (RuntimeError,KeyError,ValueError,TypeError):
            rows=[]
            for table in ['realtime_arrival_a','realtime_arrival_b','realtime_arrival']:
                rows.extend(r for r in self.store.read('realtime.db',table) if r.get('route_name')==route and str(r.get('station_id'))==station_id)
            if rows:
                row=max(rows,key=lambda r:r.get('collected_at',''))
                return dict(route=route,station_id=station_id,stale=True,source='과거 수집 기록 · 현재 도착정보 아님',
                            collected_at=row['collected_at'],headway_minutes=None,
                            buses=[dict(arrival_seconds=number(row.get(f'predict_time_sec_{n}')),remain_seats=number(row.get(f'remain_seat_cnt_{n}'))) for n in (1,2)])
            return dict(route=route,station_id=station_id,stale=True,source='조회 실패 또는 API 설정 필요',buses=[],headway_minutes=None)
