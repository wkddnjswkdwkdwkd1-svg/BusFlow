"""Read the repository's existing XLSX files with the Python standard library.
No spreadsheet rewriting, duplicate pivot ingestion, or fabricated trip times.
"""
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
import posixpath
import xml.etree.ElementTree as ET
from zipfile import ZipFile

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
HEADWAY='analysis/upstream_analysis.xlsx'
CONGESTION='analysis/station_weekday_hour_average.xlsx'
DATED='BusFlow_5003_bus_weather_by_datetime (1).xlsx'
DROPS='analysis/seat_drop_analysis.xlsx'

@lru_cache(maxsize=24)
def _sheet(path,stamp,sheet):
    with ZipFile(path) as z:
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            strings=[''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        rels={n.attrib['Id']:n.attrib['Target'] for n in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        book=ET.fromstring(z.read('xl/workbook.xml'))
        tab=next(n for n in book.find('s:sheets',NS) if n.attrib['name']==sheet)
        target=rels[tab.attrib[REL]]
        target=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
        rows=[]
        for row in ET.fromstring(z.read(target)).findall('.//s:sheetData/s:row',NS):
            values={}
            for cell in row:
                col=0
                for c in cell.attrib['r']:
                    if not c.isalpha(): break
                    col=col*26+ord(c.upper())-64
                kind=cell.attrib.get('t'); value=cell.find('s:v',NS)
                if kind=='inlineStr': v=''.join(cell.find('s:is',NS).itertext())
                elif value is None or value.text is None: v=None
                elif kind=='s': v=strings[int(value.text)]
                elif kind in ('str','e','b'): v=value.text if kind!='e' else None
                else:
                    try: v=float(value.text); v=int(v) if v.is_integer() else v
                    except ValueError: v=value.text
                values[col-1]=v
            rows.append(values)
        if not rows: return ()
        headers=rows[0]
        return tuple({str(name):row.get(i) for i,name in headers.items()} for row in rows[1:])


def sheet(root,file,name):
    p=Path(root)/file
    if not p.is_file(): return ()
    return _sheet(str(p.resolve()),p.stat().st_mtime_ns,name)


def excel_date(value):
    if isinstance(value,(int,float)): return datetime(1899,12,30)+timedelta(days=value)
    return datetime.fromisoformat(str(value))


def summary_usable(root,file,target):
    # Summary sheets have no observation dates. Do not apply them retrospectively.
    p=Path(root)/file
    if not p.is_file(): return False
    with ZipFile(p) as z:
        core=ET.fromstring(z.read('docProps/core.xml'))
        node=core.find('{http://purl.org/dc/terms/}modified')
        if node is None: return False
        return target.date()>datetime.fromisoformat(node.text.replace('Z','+00:00')).date()


def weighted(rows,key):
    valid=[(float(r[key]),int(r['sample_count'])) for r in rows if r.get(key) is not None and r.get('sample_count',0)>0]
    return sum(v*n for v,n in valid)/sum(n for _,n in valid) if valid else None


class WorkbookData:
    def __init__(self,root): self.root=Path(root); self._dated=None

    def headway(self,route,name,target,canon):
        if not summary_usable(self.root,HEADWAY,target): return None
        rows=[r for r in sheet(self.root,HEADWAY,'headway') if r['route_name']==route and r['hour']==target.hour and canon(r['station_name'])==canon(name)]
        mean=weighted(rows,'avg_headway'); seats=weighted(rows,'avg_first_seat')
        if mean is None or seats is None: return None
        count=sum(int(r['sample_count']) for r in rows)
        return dict(headway_minutes=mean,headway_p75=None,seat_median=None,seat_mean=seats,
                    full_rate=None,seat_samples=count,headway_samples=count,days=0,
                    basis='기존 엑셀의 동일 시간대 집계 (요일·계절 미분리)',source='xlsx_eta_gap_summary',
                    file=HEADWAY,sheet='headway',limited=True,
                    note='배차는 첫째·둘째 차량 ETA 차이의 표본수 가중평균입니다. 반복 조회가 포함되며 운행 시간표·차량별 독립 표본이 아닙니다. 만석 빈도와 p75는 이 표로 복원할 수 없습니다.')

    def dated(self):
        if self._dated is not None: return self._dated
        lookup={(r['노선'],int(r['정류장순번'])):str(r['정류장ID']) for r in sheet(self.root,DATED,'정류장목록')}
        result={}
        for route in ('5003A','5003B'):
            for r in sheet(self.root,DATED,route+'_날짜시간'):
                time=excel_date(r['날짜'])+timedelta(hours=int(r['시간대'][:2]))
                for key,value in r.items():
                    if not key.startswith('혼잡도_') or value is None: continue
                    sid=lookup.get((route,int(key.split('_')[1])))
                    if sid: result.setdefault((route,sid),[]).append(dict(time=time,value=float(value)))
        self._dated=result
        return result

    def congestion(self,route,station,target,comparable):
        if route.startswith('5003'):
            rows,basis=comparable(self.dated().get((route,str(station)),[]),target)
            if rows: return dict(value=round(sum(r['value'] for r in rows)/len(rows),1),sample_count=len(rows),basis=basis+' · 날짜별 정류장 평균',source=DATED,sheet=route+'_날짜시간')
        if not summary_usable(self.root,CONGESTION,target): return None
        dow=['월요일','화요일','수요일','목요일','금요일','토요일','일요일'][target.weekday()]
        rows=[r for r in sheet(self.root,CONGESTION,'전체_상세') if r['route_name']==route and str(r['station_id'])==str(station) and int(str(r['time_zone'])[:2])%24==target.hour and r['dow_nm']==(['월요일','화요일','수요일','목요일','금요일','토요일','일요일'][(target.weekday()-int(str(r['time_zone'])[:2])//24)%7]) and r['avg_congestion'] is not None]
        count=sum(int(r['data_count']) for r in rows)
        if count:
            return dict(value=round(sum(float(r['avg_congestion'])*int(r['data_count']) for r in rows)/count,1),sample_count=count,basis='동일 요일·시간 집계 (계절·공휴일 미분리)',source=CONGESTION,sheet='전체_상세')
        return None

    def sections(self,route,target):
        """Descriptive local-section travel only; never substitute for the whole commute."""
        groups={}
        for r in sheet(self.root,DROPS,'raw_drops'):
            if r['route_name']!=route: continue
            time=excel_date(r['prev_time'])
            if time.date()>=target.date() or time.hour!=target.hour: continue
            key=r['section']; groups.setdefault(key,[]).append(float(r['travel_min']))
        return [dict(section=k,mean_minutes=round(sum(v)/len(v),1),samples=len(v),
                     source=DROPS,sheet='raw_drops',basis='동일 시간대 · 요일 미분리 · 서울 시내 일부 구간') for k,v in sorted(groups.items())]

    def sources(self):
        return [dict(file=f,available=(self.root/f).is_file(),purpose=p,usage='reference' if f.endswith('.csv') else 'connected') for f,p in [
            (HEADWAY,'시간대별 배차 간격·좌석 평균'),(CONGESTION,'5001 요일·시간 혼잡도'),
            (DATED,'5003 날짜·시간·정류장 혼잡도'),(DROPS,'서울 B방향 구간 관측 기록'),
            ('data/highway_speed.csv','고속도로 속도 원자료 · 전체 버스 소요시간 아님')]]
