"""Read-only repository data audit. No external API calls or source writes."""
import csv, json, sqlite3, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from services.workbook_data_dhs_gpt_commited import sheet, HEADWAY, CONGESTION, DATED, DROPS

def audit():
    out={'databases':{},'spreadsheets':{},'csv':{}}
    for p in sorted(ROOT.rglob('*.db')):
        if '.git' in p.parts: continue
        entry={'bytes':p.stat().st_size,'tables':{}}
        try:
            with sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True) as db:
                for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                    q='"'+table.replace('"','""')+'"'
                    cols=[r[1] for r in db.execute('PRAGMA table_info('+q+')')]
                    info={'columns':cols,'rows':db.execute('SELECT COUNT(*) FROM '+q).fetchone()[0]}
                    for c in ('opr_ymd','collected_at','date','tm','updated_at'):
                        if c in cols: info[c+'_range']=db.execute(f'SELECT MIN("{c}"), MAX("{c}") FROM '+q).fetchone()
                    entry['tables'][table]=info
        except sqlite3.Error as e: entry['error']=str(e)
        out['databases'][str(p.relative_to(ROOT))]=entry
    for f,t in [(HEADWAY,'headway'),(CONGESTION,'전체_상세'),(DATED,'5003A_날짜시간'),(DATED,'5003B_날짜시간'),(DROPS,'raw_drops')]:
        rows=sheet(ROOT,f,t); out['spreadsheets'][f+' / '+t]={'rows':len(rows),'columns':list(rows[0]) if rows else []}
    p=ROOT/'data/highway_speed.csv'
    if p.exists():
        with p.open(encoding='utf-8-sig') as f: rows=list(csv.DictReader(f))
        out['csv'][p.name]={'rows':len(rows),'columns':list(rows[0]),'date_range':[min(r['date'] for r in rows),max(r['date'] for r in rows)]}
    return out
if __name__=='__main__': print(json.dumps(audit(),ensure_ascii=False,indent=2))
