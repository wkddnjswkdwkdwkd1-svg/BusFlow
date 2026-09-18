"""Existing-data recommendation. Never generate demo seats, ETAs or travel times."""
from datetime import datetime, timedelta
from services.integrated_data_dhs_gpt_commited import seasonal, number
from services.weather_delay_service_dhs_gpt_commited import get_rain_delay_weight
from services.integrated_external_dhs_gpt_commited import now_kst


def adjusted_travel(travel,route,departure,weather):
    entry=departure+timedelta(minutes=travel['pre'])
    supported=route.endswith('A') and entry.hour in (6,7,8) and travel['scope']
    weight=1.0
    if supported and weather.get('available'):
        weight=get_rain_delay_weight(seasonal(entry.month),weather['rainfall_mm_per_hour'])
    # User-requested approximation: apply the highway-derived weight only to the
    # requested portion inside Giheung -> Sinnonhyeon. Local segments are unchanged.
    total=travel['pre']+travel['core']*weight+travel['after']
    return dict(minutes=total,weight=weight,weather_applied=supported and weather.get('available',False),
                pre_minutes=travel['pre'],core_minutes=travel['core']*weight,after_minutes=travel['after'],
                extra_minutes=travel['core']*(weight-1),scope_supported=bool(supported))


def recommend(store,external,payload,now=None):
    now=now or now_kst()
    try:
        date=datetime.strptime(payload['date'],'%Y-%m-%d').date()
        start_text=payload.get('departure_time') or '05:00'
        end_text=payload.get('arrival_time') or '23:59'
        start=datetime.strptime(f'{date} {start_text}','%Y-%m-%d %H:%M')
        deadline=datetime.strptime(f'{date} {end_text}','%Y-%m-%d %H:%M')
    except (KeyError,TypeError,ValueError): raise ValueError('날짜·시간을 확인해주세요.')
    if date<now.date(): raise ValueError('추천 날짜는 오늘 이후로 선택해주세요.')
    if deadline<=start: raise ValueError('도착 마감은 출발 가능시각보다 늦어야 합니다.')
    if date==now.date(): start=max(start,now.replace(second=0,microsecond=0)+timedelta(minutes=1))
    selected=payload.get('selected_boarding_stations') or []
    if not isinstance(selected,list) or not 1<=len(selected)<=40: raise ValueError('승차 후보를 1~40개 선택해주세요.')
    destination=payload.get('destination')
    if not destination: raise ValueError('도착 정류장을 선택해주세요.')
    mode=payload.get('commute_mode','morning')
    if mode not in ('morning','evening'): raise ValueError('출퇴근 구분을 확인해주세요.')
    variant='A' if mode=='morning' else 'B'
    candidates=[]; missing=set(); considered=0; late_count=0; insufficient=0; seen=set(); weather_cache={}
    for selection in selected:
        route=str(selection.get('route_name') or str(selection.get('route',''))+variant)
        spec=store.catalog.get(route)
        if not spec or not route.endswith(variant): raise ValueError('선택한 노선과 이동 구간이 맞지 않습니다.')
        if payload.get('route_family') not in (None,'all',route[:4]): raise ValueError('선택한 노선 범위가 맞지 않습니다.')
        station=next((s for s in spec['boarding'] if str(s['id'])==str(selection.get('id'))),None)
        if not station: raise ValueError('서버 목록에 없는 승차 정류장입니다.')
        if (route,station['id']) in seen: continue
        seen.add((route,station['id']))
        dest=store.resolve_destination(route,destination)
        if not dest or not dest.get('realtime_id'):
            missing.add(f'{route} · {destination}: 해당 방향의 도착 정류장 기록 없음'); insufficient+=1; continue
        slot=start
        while slot<deadline:
            profile=store.profile(route,station['realtime_id'],slot)
            if not profile:
                missing.add(f'{route} · {station["name"]} · {slot.hour:02d}시: 과거 배차·좌석 표본 부족')
                insufficient+=1; slot+=timedelta(minutes=10); continue
            # Historical forecast, not a published timetable: expected initial wait
            # plus an explicit geometric missed-bus approximation from historical full rate.
            rate=profile['full_rate']
            if rate is not None and rate>=1:
                missing.add(f'{route} · {station["name"]} · {slot.hour:02d}시: 관측 좌석이 모두 만석')
                slot+=timedelta(minutes=10); continue
            headway=profile['headway_p75'] or profile['headway_minutes']
            wait=headway/2
            missed=headway*rate/(1-rate) if rate is not None else 0
            departure=slot+timedelta(minutes=wait+missed)
            if departure>=deadline: late_count+=1; slot+=timedelta(minutes=10); continue
            travel=store.travel(route,station['realtime_id'],dest['realtime_id'],departure)
            if not travel:
                missing.add(f'{route} · {station["name"]} → {dest["name"]} · {departure.hour:02d}시: 구간 이동시간 표본 없음')
                insufficient+=1; slot+=timedelta(minutes=10); continue
            entry=departure+timedelta(minutes=travel['pre'])
            weather_key=entry.replace(minute=0,second=0,microsecond=0)
            if weather_key not in weather_cache: weather_cache[weather_key]=external.weather(entry,'giheung')
            weather=weather_cache[weather_key]
            adj=adjusted_travel(travel,route,departure,weather)
            arrival=departure+timedelta(minutes=adj['minutes']); considered+=1
            if arrival>deadline: late_count+=1; slot+=timedelta(minutes=10); continue
            margin=(deadline-arrival).total_seconds()/60
            congestion=store.congestion(route,station.get('historical_id'),departure)
            risk=rate if rate is not None else 1.0
            grade='안전' if risk<=.1 and margin>=15 else '보통' if risk<=.3 and margin>=5 else '주의'
            # Sparse history or uncorrected weather must not be presented as high confidence.
            limited=profile['days']<3 or profile['seat_samples']<10 or travel['sample_count']<5
            if limited or not weather.get('available'): grade='주의'
            reasons=[f'{profile["basis"]} 기록에서 배차·좌석을 추정했습니다.',
                     f'이동시간은 {travel["basis"]} 구간 기록 {travel["sample_count"]}건을 사용했습니다.',
                     '정류장 도착 이후의 예상 대기를 포함합니다. 도보시간은 포함하지 않습니다.']
            if profile.get('note'): reasons.append(profile['note'])
            if rate is None: reasons.append('만석 빈도 자료가 없어 추가 만석 대기는 계산하지 못했습니다. 표시 도착시각은 만석 지연을 포함하지 않으며 안전도는 주의입니다.')
            if missed>0: reasons.append(f'과거 만석 빈도와 배차를 이용한 추가 대기 추정 {missed:.1f}분을 포함했습니다.')
            if adj['weather_applied']: reasons.append('기흥역→신논현역 범위에만 날씨 가중치를 적용했습니다.')
            elif adj['scope_supported']: reasons.append('예보를 받지 못해 날씨 보정 없이 기존 이동시간을 표시합니다.')
            else: reasons.append('날씨 보정 지원 구간·시간 밖이므로 기존 구간 이동시간을 사용합니다.')
            if limited: reasons.append('표본이 적어 안전도는 주의로 표시합니다.')
            c=dict(route=route,boarding_station=station['name'],destination=dest['name'],
                   station_ready_time=slot.strftime('%H:%M'),departure_time=departure.strftime('%H:%M'),
                   estimated_arrival_time=arrival.strftime('%H:%M'),arrival_datetime=arrival.isoformat(),
                   arrival_seconds=round((wait+missed)*60),remain_seats=profile['seat_median'] if profile['seat_median'] is not None else round(profile['seat_mean'],1),
                   seat_statistic='평균' if profile['seat_median'] is None else '중앙값',
                   full_rate_known=rate is not None,
                   headway_minutes=profile['headway_minutes'],missed_bus_delay_minutes=round(missed,1),
                   travel_time_minutes=round(adj['minutes'],1),margin_minutes=round(margin,1),
                   stability_grade=grade,deadline_met=True,source='과거 기록 기반 예측 · 실시간 차량 아님',
                   historical_congestion=congestion,profile_samples=profile['seat_samples'],history_days=profile['days'],
                   segment_minutes=dict(local_before=round(adj['pre_minutes'],1),giheung_sinnonhyeon=round(adj['core_minutes'],1),local_after=round(adj['after_minutes'],1)),
                   weather_applied=adj['weather_applied'],reasons=reasons,
                   _risk=risk,_limited=limited)
            candidates.append(c)
            slot+=timedelta(minutes=10)
    if payload.get('risk_mode')=='fast':
        candidates.sort(key=lambda c:(c['arrival_datetime'],c['_risk'],c['_limited']))
    else:
        candidates.sort(key=lambda c:(c['_limited'],c['_risk'],c['historical_congestion']['value'] if c['historical_congestion'] else float('inf'),-c['margin_minutes']))
    # Preserve a useful variety of stops and routes, then fill remaining cards.
    chosen=[]; combinations=set()
    for c in candidates:
        key=(c['route'],c['boarding_station'])
        if key not in combinations: chosen.append(c); combinations.add(key)
        if len(chosen)==4: break
    for c in candidates:
        if len(chosen)==4: break
        if c not in chosen: chosen.append(c)
    chosen.sort(key=lambda c:candidates.index(c))
    for rank,c in enumerate(chosen,1):
        c['rank']=rank; c['id']=f'candidate-{rank}'; c.pop('_risk'); c.pop('_limited')
    status='ok' if chosen else 'insufficient_data' if insufficient or (not considered and not late_count) else 'no_feasible_route'
    message='조건에 맞는 후보를 비교했습니다.' if chosen else '기존 자료가 부족해 도착 가능한 경로를 판단할 수 없습니다.' if status=='insufficient_data' else '해당 시간 내 도착 가능한 경로가 없습니다.'
    return dict(status=status,message=message,candidates=chosen,recommended=chosen[0] if chosen else None,
                missing_data=sorted(missing),coverage_complete=not missing,considered=considered,
                late_candidates=late_count,request=payload,
                station_evidence=station_evidence(store,selected,start),
                local_section_evidence={route:store.workbooks.sections(route,start) for route in sorted({r for r,_ in seen})},
                model_note='안전도는 검증된 도착 확률이 아닌 과거 표본 기반 등급입니다. 미래 승차시각은 운행 시간표가 아닌 추정입니다.')


def station_evidence(store,selections,target):
    evidence=[]; seen=set()
    for selection in selections:
        route=selection.get('route_name')
        if route not in store.catalog: continue
        stop=next((s for s in store.catalog[route]['boarding'] if str(s['id'])==str(selection.get('id'))),None)
        if not stop or (route,stop['id']) in seen: continue
        seen.add((route,stop['id']))
        profile=store.profile(route,stop['realtime_id'],target)
        congestion=store.congestion(route,stop.get('historical_id'),target)
        evidence.append(dict(route=route,station=stop['name'],hour=target.hour,profile=profile,congestion=congestion))
    return evidence
