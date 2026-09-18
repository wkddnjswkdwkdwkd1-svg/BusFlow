# BusFlow `dhs_gpt_commited` overlay

이 구현은 **친구가 만든 기존 파일을 수정하지 않고**, 별도 파일을 추가해서 기존 Flask 앱 위에 기능을 덧붙이는 방식입니다.

## 실행

```bash
python app_dhs_gpt_commited.py
```

법정공휴일까지 자동 판별하려면 선택적으로:

```bash
pip install -r requirements_dhs_gpt_commited.txt
```

`holidays`가 없어도 앱은 실행되며, 그 경우 토/일만 휴일로 처리합니다.

## 이번 버전에서 완료한 것

- 기존 `app.py` 수정 없음
- 기존 `services/highway_predictor.py` 수정 없음
- 기존 `Time_Keeper_mid_prototype.html` 수정 없음
- 기존 HTML의 날씨 placeholder 영역만 런타임에 실제 기상 지연 안내로 채움
- 기존 결과 카드의 빈 metric 공간에 `예상 소요시간`, `예상 도착`만 추가
- 경로 결과에는 강수 지연률 숫자를 노출하지 않음
- 결과 화면에는 필요할 때 `기상 예보가 예상 소요시간에 반영되었습니다.` 정도만 표시
- 2022·2025·2026 데이터에서 계산한 계절별 baseline 사용
- 겨울 12~2 / 봄 3~5 / 여름 6~8 / 가을 9~11
- 평일/휴일 × 06/07/08시 baseline
- 강수 여부가 아니라 **시간당 강수량(mm/h)** 기반 지연 가중치
- 강수량 knot 사이 선형 보간
- 메인 날씨 카드 아래에서만 `비 때문에 평소보다 몇 % 지연되는지` 안내
- 경로 결과에는 기상보정이 들어간 최종 예상 소요시간/도착시각 표시
- 프론트의 5001 / 5003 선택을 `/api/recommend-v2`에 반영
- `safe` / `fast` 선택을 추천 후보 정렬에 반영
- 과거 replay 날짜는 `data/weather.db` 사용
- 현재/미래는 기상청 단기예보 API 사용
- 출근 06~08시 이외에는 검증되지 않은 새 고속도로 모델을 억지로 적용하지 않음
- 기존 고정 `rainfall > 0 -> ×0.92` 보정은 새 `/api/recommend-v2`의 출근 모델 경로에서는 사용하지 않음

## 중요한 계산 방식

친구 코드의 `travel_time_stats.p75_minutes`는 전체 경로 통계로 유지합니다.

새 기상모델이 계산한 것은 고속도로 22.8 km 구간의 강우 추가 지연량입니다.

```text
최종 전체 이동시간
= 기존 전체경로 p75
+ (기상보정 고속도로 시간 - 무강수 고속도로 baseline)
```

따라서 로컬도로/정류장 구간을 새 고속도로 시간과 중복해서 더하지 않습니다.

## HTML 적용 방식

원본 HTML 파일은 그대로 둡니다. `app_dhs_gpt_commited.py`가 원본 HTML을 읽은 뒤 응답 시점에만 다음을 삽입합니다.

- 날씨 카드의 기존 placeholder → `weatherDelayNotice`
- `Time_Keeper_ui_dhs_gpt_commited.css`
- `Time_Keeper_ui_dhs_gpt_commited.js`

따라서 친구의 HTML을 직접 덮어쓰지 않으면서 기존 디자인과 빈 공간만 활용합니다.

## 날씨 API

### 현재/예보

```text
GET /api/weather
GET /api/weather?datetime=2026-09-18T07:00:00&region=yongin
GET /api/weather?datetime=2026-09-18T18:00:00&region=seoul
```

환경변수:

```text
KMA_API_KEY=...
```

encoded service key도 내부에서 `unquote` 처리합니다.

### 과거 replay

요청 시각이 과거이면 `data/weather.db`를 먼저 사용합니다.

## 디버그 API

```text
GET /api/dhs_gpt_commited/health
GET /api/dhs_gpt_commited/weather-delay?date=2026-07-15&hour=7&rainfall=4.2
```

## 모델 신뢰 범위

현재 기상 보정 고속도로 모델의 실측 학습/검증 범위는 **평일/휴일 출근시간 06~08시**입니다.

퇴근시간에는 별도의 저녁 VDS 학습데이터가 충분히 준비되기 전까지 새 기상 가중치를 적용하지 않습니다.

## 추가 파일

- `app_dhs_gpt_commited.py`
- `README_dhs_gpt_commited.md`
- `requirements_dhs_gpt_commited.txt`
- `Time_Keeper_ui_dhs_gpt_commited.js`
- `Time_Keeper_ui_dhs_gpt_commited.css`
- `services/highway_predictor_dhs_gpt_commited.py`
- `services/weather_delay_service_dhs_gpt_commited.py`
- `services/weather_delay_config_dhs_gpt_commited.json`
- `services/weather_forecast_dhs_gpt_commited.py`
- `services/recommendation_postprocess_dhs_gpt_commited.py`
- `tests/test_weather_delay_dhs_gpt_commited.py`
