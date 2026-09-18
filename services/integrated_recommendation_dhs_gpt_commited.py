"""
BusFlow Integrated Recommendation Engine

최종 구조
--------
1. 실시간 ETA / 좌석 / 배차 확인
2. 부족하면 과거 배차 / 좌석 profile 사용
3. 그래도 부족하면 노선 기본값 fallback

4. Highway ML
   -> 고속도로 속도 / 이동시간 예측

5. Congestion ML
   -> 정류장별 예상 혼잡도 예측

6. 실제 이동시간 자료가 부족하면
   -> 최소 이동시간 fallback 사용

7. 도착 마감시간을 만족하는 후보 생성

8. 사용자가 출발시간을 직접 입력하지 않은 경우
   -> 첫차가 아니라
      "도착 마감에 맞춰 가능한 한 늦게 출발"
      하는 시간을 추천
"""

from datetime import datetime, timedelta

from services.integrated_external_dhs_gpt_commited import (
    now_kst,
)

from services.weather_delay_service_dhs_gpt_commited import (
    get_rain_delay_weight,
)

from services.integrated_data_dhs_gpt_commited import (
    seasonal,
)

from services.highway_predictor import (
    predict_highway_time,
)

from services.congestion_predictor import (
    predict_congestion,
)


# ============================================================
# 추천 기본 설정
# ============================================================

# 실시간/과거 배차 자료가 모두 없을 때
# 마지막 fallback
DEFAULT_HEADWAY_MINUTES = {
    "5001A": 10.0,
    "5003A": 10.0,
    "5001B": 10.0,
    "5003B": 10.0,
}


# 출발시간 추천받기에서
# 안정 우선 시 확보하려는 도착 여유
SAFE_MARGIN_MINUTES = 15


# 출발시간 추천받기 시
# 도착 마감 기준으로 몇 분 전까지 탐색할지
FLEXIBLE_LOOKBACK_MINUTES = 180


# 추천 신뢰도
CONFIDENCE_HIGH = "높음"
CONFIDENCE_MEDIUM = "보통"
CONFIDENCE_LOW = "낮음"


# ============================================================
# Highway ML 설정
# ============================================================

# 학습 데이터 평균속도
HIGHWAY_BASELINE_SPEED = 64.77

# 분석 고속도로 구간 길이
HIGHWAY_DISTANCE_KM = 22.8

# 평상시 고속도로 이동시간
HIGHWAY_BASELINE_MINUTES = (
    HIGHWAY_DISTANCE_KM
    / HIGHWAY_BASELINE_SPEED
    * 60
)


# ============================================================
# 최종 이동시간 fallback
# ============================================================

# 정확한 구간 이동시간 자료가 없는 경우만 사용.
#
# 1차 제출에서 추천 화면이 완전히 비는 것을
# 방지하기 위한 마지막 fallback이다.
#
# 이후 데이터가 충분해지면 실제 노선 평균으로
# 교체하는 것이 좋다.

FALLBACK_TRAVEL_MINUTES = {
    "5001A": 50.0,
    "5003A": 55.0,
    "5001B": 55.0,
    "5003B": 60.0,
}


# ============================================================
# 승차 정보 통합
# ============================================================

def get_boarding_context(
    store,
    external,
    route,
    station,
    target,
):
    """
    승차 정보를 다음 순서로 확보한다.

    1. 실시간 API
    2. 과거 profile
    3. 기본값 fallback

    반환:
        profile
        live
        headway_minutes
        remain_seats
        full_rate
        confidence
        source
    """

    live = None
    profile = None


    # --------------------------------------------------------
    # 실시간 정보
    # --------------------------------------------------------

    try:

        live = external.live(
            route,
            station["realtime_id"],
        )

    except Exception:

        live = None


    # --------------------------------------------------------
    # 과거 profile
    # --------------------------------------------------------

    try:

        profile = store.profile(
            route,
            station["realtime_id"],
            target,
        )

    except Exception:

        profile = None


    # --------------------------------------------------------
    # 실시간 데이터 유효 여부
    # --------------------------------------------------------

    has_live = bool(
        live
        and not live.get("stale")
    )

    has_profile = bool(
        profile
    )


    # --------------------------------------------------------
    # 배차간격
    # --------------------------------------------------------

    headway = None
    headway_source = None


    # 1순위: 실시간 두 차량 간격
    if (
        has_live
        and live.get("headway_minutes") is not None
    ):

        headway = live[
            "headway_minutes"
        ]

        headway_source = (
            "실시간 배차"
        )


    # 2순위: 과거 profile
    elif has_profile:

        headway = (
            profile.get("headway_p75")
            or profile.get("headway_minutes")
        )

        if headway is not None:

            headway_source = (
                "과거 배차"
            )


    # 3순위: 기본값
    if headway is None:

        headway = (
            DEFAULT_HEADWAY_MINUTES.get(
                route,
                10.0,
            )
        )

        headway_source = (
            "기본 배차 fallback"
        )


    # 비정상 배차 방어
    if headway <= 0:

        headway = (
            DEFAULT_HEADWAY_MINUTES.get(
                route,
                10.0,
            )
        )

        headway_source = (
            "기본 배차 fallback"
        )


    # --------------------------------------------------------
    # 좌석
    # --------------------------------------------------------

    remain_seats = None
    seat_source = None


    # 1순위: 실시간 첫 버스
    if (
        has_live
        and live.get("buses")
    ):

        first_bus = (
            live["buses"][0]
        )

        live_seat = (
            first_bus.get(
                "remain_seats"
            )
        )

        if live_seat is not None:

            remain_seats = (
                live_seat
            )

            seat_source = (
                "실시간 좌석"
            )


    # 2순위: 과거 좌석
    if (
        remain_seats is None
        and has_profile
    ):

        if (
            profile.get(
                "seat_median"
            )
            is not None
        ):

            remain_seats = (
                profile[
                    "seat_median"
                ]
            )

            seat_source = (
                "과거 좌석 중앙값"
            )

        elif (
            profile.get(
                "seat_mean"
            )
            is not None
        ):

            remain_seats = (
                profile[
                    "seat_mean"
                ]
            )

            seat_source = (
                "과거 좌석 평균"
            )


    # --------------------------------------------------------
    # 과거 만석 빈도
    # --------------------------------------------------------

    full_rate = None


    if has_profile:

        full_rate = (
            profile.get(
                "full_rate"
            )
        )


    # --------------------------------------------------------
    # 신뢰도
    # --------------------------------------------------------

    if (
        has_live
        and has_profile
    ):

        confidence = (
            CONFIDENCE_HIGH
        )

    elif (
        has_live
        or has_profile
    ):

        confidence = (
            CONFIDENCE_MEDIUM
        )

    else:

        confidence = (
            CONFIDENCE_LOW
        )


    return {

        "profile":
            profile,

        "live":
            live,

        "headway_minutes":
            float(
                headway
            ),

        "headway_source":
            headway_source,

        "remain_seats":
            remain_seats,

        "seat_source":
            seat_source,

        "full_rate":
            full_rate,

        "confidence":
            confidence,
    }


# ============================================================
# 이동시간 확보
# ============================================================

def get_travel_context(
    store,
    route,
    station,
    destination,
    departure,
):
    """
    이동시간 데이터 확보.

    1. 실제 과거 차량 이동시간
    2. 없으면 최종 fallback

    항상 travel dict를 반환한다.
    """

    try:

        travel = store.travel(
            route,
            station["realtime_id"],
            destination["realtime_id"],
            departure,
        )

    except Exception:

        travel = None


    # --------------------------------------------------------
    # 실제 데이터 존재
    # --------------------------------------------------------

    if travel:

        result = dict(
            travel
        )

        result[
            "fallback"
        ] = False

        return result


    # --------------------------------------------------------
    # 마지막 fallback
    # --------------------------------------------------------

    fallback_minutes = (
        FALLBACK_TRAVEL_MINUTES.get(
            route,
            55.0,
        )
    )


    return {

        "pre":
            0.0,

        "core":
            fallback_minutes,

        "after":
            0.0,

        "scope":
            route.endswith("A"),

        "sample_count":
            0,

        "basis":
            "노선 기본 이동시간 fallback",

        "fallback":
            True,
    }


# ============================================================
# Highway ML 적용
# ============================================================

def adjusted_travel(
    travel,
    route,
    departure,
    weather,
):
    """
    기본 이동시간에 Highway ML 결과를 반영한다.

    Highway ML 지원 범위:
        A 방향
        평일
        오전 06~08시

    ML 사용 불가능 시:
        기존 강수 가중치 fallback
    """

    entry = (
        departure
        + timedelta(
            minutes=travel["pre"]
        )
    )


    # --------------------------------------------------------
    # Highway 보정 지원 구간
    # --------------------------------------------------------

    scope_supported = bool(
        route.endswith("A")
        and entry.hour in (
            6,
            7,
            8,
        )
        and travel.get(
            "scope"
        )
    )


    adjusted_core = (
        travel["core"]
    )

    traffic_weight = 1.0

    highway_prediction = None

    highway_ml_applied = False

    rain_fallback_applied = False


    # --------------------------------------------------------
    # ML 사용 가능 여부
    # --------------------------------------------------------

    ml_supported = bool(
        scope_supported
        and entry.weekday() <= 4
        and weather.get(
            "available"
        )
        and weather.get(
            "temperature"
        ) is not None
        and weather.get(
            "humidity"
        ) is not None
        and weather.get(
            "wind_speed"
        ) is not None
    )


    # --------------------------------------------------------
    # Highway ML
    # --------------------------------------------------------

    if ml_supported:

        try:

            highway_prediction = (
                predict_highway_time(
                    hour=entry.hour,
                    day_of_week=(
                        entry.weekday()
                    ),
                    temperature=(
                        weather[
                            "temperature"
                        ]
                    ),
                    humidity=(
                        weather[
                            "humidity"
                        ]
                    ),
                    wind_speed=(
                        weather[
                            "wind_speed"
                        ]
                    ),
                    rainfall=(
                        weather.get(
                            "rainfall_mm_per_hour",
                            0.0,
                        )
                    ),
                )
            )


            predicted_minutes = (
                highway_prediction[
                    "highway_minutes"
                ]
            )


            # 평상시 고속도로 시간 대비
            # 오늘의 ML 예상 비율
            traffic_weight = (
                predicted_minutes
                / HIGHWAY_BASELINE_MINUTES
            )


            # core 구간에 해당 비율 적용
            adjusted_core = (
                travel["core"]
                * traffic_weight
            )


            highway_ml_applied = True


        except Exception:

            highway_prediction = None

            highway_ml_applied = False


    # --------------------------------------------------------
    # ML 실패 → 기존 강수 보정
    # --------------------------------------------------------

    if (
        not highway_ml_applied
        and scope_supported
        and weather.get(
            "available"
        )
    ):

        rainfall = (
            weather.get(
                "rainfall_mm_per_hour",
                0.0,
            )
        )


        traffic_weight = (
            get_rain_delay_weight(
                seasonal(
                    entry.month
                ),
                rainfall,
            )
        )


        adjusted_core = (
            travel["core"]
            * traffic_weight
        )


        rain_fallback_applied = True


    # --------------------------------------------------------
    # 전체 이동시간
    # --------------------------------------------------------

    total = (
        travel["pre"]
        + adjusted_core
        + travel["after"]
    )


    extra_minutes = (
        adjusted_core
        - travel["core"]
    )


    return {

        "minutes":
            total,

        "pre_minutes":
            travel["pre"],

        "core_minutes":
            adjusted_core,

        "after_minutes":
            travel["after"],

        "extra_minutes":
            extra_minutes,

        "traffic_weight":
            traffic_weight,

        "scope_supported":
            scope_supported,

        "weather_applied":
            (
                highway_ml_applied
                or rain_fallback_applied
            ),

        "highway_ml_applied":
            highway_ml_applied,

        "rain_fallback_applied":
            rain_fallback_applied,

        "highway_prediction":
            highway_prediction,
    }


# ============================================================
# Congestion ML
# ============================================================

def predict_candidate_congestion(
    route,
    station,
    departure,
):
    """
    미래 혼잡도 예측.

    지원:
        5001A
        5003A
        오전 06~10시
    """

    if route not in (
        "5001A",
        "5003A",
    ):

        return None


    station_seq = (
        station.get(
            "seq"
        )
    )


    if station_seq is None:

        return None


    if not (
        6
        <= departure.hour
        <= 10
    ):

        return None


    try:

        result = (
            predict_congestion(
                route_name=route,
                station_seq=(
                    station_seq
                ),
                date=(
                    departure.strftime(
                        "%Y-%m-%d"
                    )
                ),
                hour=(
                    departure.hour
                ),
            )
        )


        return result[
            "predicted_congestion"
        ]


    except Exception:

        return None


# ============================================================
# 후보 혼잡도 정렬값
# ============================================================

def safe_congestion_value(
    candidate,
):
    """
    ML 혼잡도 우선.

    ML 결과가 없으면
    기존 historical congestion 사용.
    """

    predicted = (
        candidate.get(
            "predicted_congestion"
        )
    )


    if predicted is not None:

        return predicted


    historical = (
        candidate.get(
            "historical_congestion"
        )
    )


    if historical:

        value = (
            historical.get(
                "value"
            )
        )

        if value is not None:

            return value


    return float(
        "inf"
    )


# ============================================================
# 시간 문자열 → 분
# ============================================================

def time_to_minutes(
    text,
):
    """
    '07:35' → 455
    """

    hour, minute = map(
        int,
        text.split(":"),
    )

    return (
        hour * 60
        + minute
    )


# ============================================================
# 메인 추천
# ============================================================

def recommend(
    store,
    external,
    payload,
    now=None,
):

    now = (
        now
        or now_kst()
    )


    # ========================================================
    # 날짜
    # ========================================================

    try:

        date = (
            datetime.strptime(
                payload["date"],
                "%Y-%m-%d",
            )
            .date()
        )

    except (
        KeyError,
        TypeError,
        ValueError,
    ):

        raise ValueError(
            "날짜를 확인해주세요."
        )


    # ========================================================
    # 출발시간 추천 여부
    # ========================================================

    departure_flexible = bool(
        payload.get(
            "departure_flexible",
            False,
        )
    )


    # ========================================================
    # 도착 마감
    # ========================================================

    end_text = (
        payload.get(
            "arrival_time"
        )
        or "23:59"
    )


    try:

        deadline = (
            datetime.strptime(
                f"{date} {end_text}",
                "%Y-%m-%d %H:%M",
            )
        )

    except ValueError:

        raise ValueError(
            "도착 마감시간을 확인해주세요."
        )


    # ========================================================
    # 시작시간
    # ========================================================

    if departure_flexible:

        # 첫차부터 찾는 것이 아니라
        # 마감시간 기준 일정 시간 전부터 탐색
        start = (
            deadline
            - timedelta(
                minutes=(
                    FLEXIBLE_LOOKBACK_MINUTES
                )
            )
        )


        earliest = (
            datetime.combine(
                date,
                datetime.strptime(
                    "05:00",
                    "%H:%M",
                ).time(),
            )
        )


        start = max(
            start,
            earliest,
        )


    else:

        start_text = (
            payload.get(
                "departure_time"
            )
            or "07:00"
        )


        try:

            start = (
                datetime.strptime(
                    f"{date} {start_text}",
                    "%Y-%m-%d %H:%M",
                )
            )

        except ValueError:

            raise ValueError(
                "출발시간을 확인해주세요."
            )


    # ========================================================
    # 기본 검증
    # ========================================================

    if date < now.date():

        raise ValueError(
            "추천 날짜는 오늘 이후로 선택해주세요."
        )


    if deadline <= start:

        raise ValueError(
            "도착 마감은 출발 가능시각보다 늦어야 합니다."
        )


    if date == now.date():

        now_limit = (
            now.replace(
                second=0,
                microsecond=0,
            )
            + timedelta(
                minutes=1
            )
        )

        start = max(
            start,
            now_limit,
        )


    # ========================================================
    # 승차 후보
    # ========================================================

    selected = (
        payload.get(
            "selected_boarding_stations"
        )
        or []
    )


    if (
        not isinstance(
            selected,
            list,
        )
        or not (
            1
            <= len(selected)
            <= 40
        )
    ):

        raise ValueError(
            "승차 후보를 1~40개 선택해주세요."
        )


    # ========================================================
    # 목적지
    # ========================================================

    destination = (
        payload.get(
            "destination"
        )
    )


    if not destination:

        raise ValueError(
            "도착 정류장을 선택해주세요."
        )


    # ========================================================
    # 출퇴근 방향
    # ========================================================

    mode = (
        payload.get(
            "commute_mode",
            "morning",
        )
    )


    if mode not in (
        "morning",
        "evening",
    ):

        raise ValueError(
            "출퇴근 구분을 확인해주세요."
        )


    variant = (
        "A"
        if mode == "morning"
        else "B"
    )


    # ========================================================
    # 결과 저장
    # ========================================================

    candidates = []

    missing = set()

    considered = 0

    late_count = 0

    seen = set()

    weather_cache = {}


    # ========================================================
    # 정류장 반복
    # ========================================================

    for selection in selected:


        route = str(
            selection.get(
                "route_name"
            )
            or (
                str(
                    selection.get(
                        "route",
                        "",
                    )
                )
                + variant
            )
        )


        spec = (
            store.catalog.get(
                route
            )
        )


        if (
            not spec
            or not route.endswith(
                variant
            )
        ):

            continue


        # ----------------------------------------------------
        # 정류장 찾기
        # ----------------------------------------------------

        station = next(
            (
                stop
                for stop
                in spec[
                    "boarding"
                ]
                if str(
                    stop[
                        "id"
                    ]
                )
                == str(
                    selection.get(
                        "id"
                    )
                )
            ),
            None,
        )


        if not station:

            continue


        station_key = (
            route,
            station[
                "id"
            ],
        )


        if station_key in seen:

            continue


        seen.add(
            station_key
        )


        # ----------------------------------------------------
        # 목적지
        # ----------------------------------------------------

        dest = (
            store.resolve_destination(
                route,
                destination,
            )
        )


        if (
            not dest
            or not dest.get(
                "realtime_id"
            )
        ):

            missing.add(
                f"{route} · "
                f"{destination}: "
                "도착 정류장 매칭 부족"
            )

            continue


        # ====================================================
        # 시간 후보
        # ====================================================

        slot = start


        while slot < deadline:


            # ------------------------------------------------
            # 승차 정보
            # ------------------------------------------------

            boarding = (
                get_boarding_context(
                    store=store,
                    external=external,
                    route=route,
                    station=station,
                    target=slot,
                )
            )


            profile = (
                boarding[
                    "profile"
                ]
            )


            live_info = (
                boarding[
                    "live"
                ]
            )


            headway = (
                boarding[
                    "headway_minutes"
                ]
            )


            full_rate = (
                boarding[
                    "full_rate"
                ]
            )


            confidence = (
                boarding[
                    "confidence"
                ]
            )


            remain_seats = (
                boarding[
                    "remain_seats"
                ]
            )


            # ------------------------------------------------
            # 대기시간
            # ------------------------------------------------

            live_first_bus = None


            if (
                live_info
                and not live_info.get(
                    "stale"
                )
                and live_info.get(
                    "buses"
                )
            ):

                live_first_bus = (
                    live_info[
                        "buses"
                    ][0]
                )


            if (
                live_first_bus
                and live_first_bus.get(
                    "arrival_seconds"
                )
                is not None
            ):

                wait = (
                    live_first_bus[
                        "arrival_seconds"
                    ]
                    / 60
                )


            else:

                wait = (
                    headway
                    / 2
                )


            # ------------------------------------------------
            # 만석으로 인한 추가 대기
            # ------------------------------------------------

            if (
                remain_seats is not None
                and remain_seats > 0
                and live_first_bus
            ):

                # 현재 첫 버스 좌석이 있으면
                # 실시간 정보 우선
                missed = 0


            elif (
                full_rate is not None
                and 0
                <= full_rate
                < 1
            ):

                missed = (
                    headway
                    * full_rate
                    / (
                        1
                        - full_rate
                    )
                )


            elif (
                full_rate is not None
                and full_rate >= 1
            ):

                # 과거에 모두 만석이면
                # 최소 다음 한 대까지 기다리는 것으로
                # 보수적으로 처리
                missed = (
                    headway
                )


            else:

                missed = 0


            # ------------------------------------------------
            # 예상 탑승시간
            # ------------------------------------------------

            departure = (
                slot
                + timedelta(
                    minutes=(
                        wait
                        + missed
                    )
                )
            )


            if departure >= deadline:

                late_count += 1

                slot += timedelta(
                    minutes=10
                )

                continue


            # ------------------------------------------------
            # 이동시간
            # ------------------------------------------------

            travel = (
                get_travel_context(
                    store=store,
                    route=route,
                    station=station,
                    destination=dest,
                    departure=departure,
                )
            )


            # travel fallback이면
            # 추천 신뢰도 낮춤
            if travel.get(
                "fallback"
            ):

                confidence = (
                    CONFIDENCE_LOW
                )


            # ------------------------------------------------
            # 고속도로 진입 예상시간
            # ------------------------------------------------

            entry = (
                departure
                + timedelta(
                    minutes=(
                        travel[
                            "pre"
                        ]
                    )
                )
            )


            weather_key = (
                entry.replace(
                    minute=0,
                    second=0,
                    microsecond=0,
                )
            )


            # ------------------------------------------------
            # 날씨
            # ------------------------------------------------

            if (
                weather_key
                not in weather_cache
            ):

                weather_cache[
                    weather_key
                ] = (
                    external.weather(
                        entry,
                        "giheung",
                    )
                )


            weather = (
                weather_cache[
                    weather_key
                ]
            )


            # ------------------------------------------------
            # Highway ML
            # ------------------------------------------------

            adjusted = (
                adjusted_travel(
                    travel=travel,
                    route=route,
                    departure=departure,
                    weather=weather,
                )
            )


            # ------------------------------------------------
            # 예상 도착시간
            # ------------------------------------------------

            arrival = (
                departure
                + timedelta(
                    minutes=(
                        adjusted[
                            "minutes"
                        ]
                    )
                )
            )


            considered += 1


            if arrival > deadline:

                late_count += 1

                slot += timedelta(
                    minutes=10
                )

                continue


            # ------------------------------------------------
            # 도착 마감 여유
            # ------------------------------------------------

            margin = (
                (
                    deadline
                    - arrival
                )
                .total_seconds()
                / 60
            )


            # ------------------------------------------------
            # 기존 과거 혼잡도
            # ------------------------------------------------

            try:

                historical_congestion = (
                    store.congestion(
                        route,
                        station.get(
                            "historical_id"
                        ),
                        departure,
                    )
                )

            except Exception:

                historical_congestion = (
                    None
                )


            # ------------------------------------------------
            # Congestion ML
            # ------------------------------------------------

            predicted_congestion = (
                predict_candidate_congestion(
                    route=route,
                    station=station,
                    departure=departure,
                )
            )


            # ------------------------------------------------
            # 위험도
            # ------------------------------------------------

            if (
                full_rate is not None
            ):

                risk = min(
                    max(
                        float(
                            full_rate
                        ),
                        0.0,
                    ),
                    1.0,
                )


            elif (
                remain_seats
                is not None
            ):

                if remain_seats <= 0:

                    risk = 1.0

                elif remain_seats <= 5:

                    risk = 0.5

                elif remain_seats <= 10:

                    risk = 0.25

                else:

                    risk = 0.1


            else:

                risk = 0.5


            # ------------------------------------------------
            # 데이터 부족 여부
            # ------------------------------------------------

            profile_days = (
                profile.get(
                    "days",
                    0,
                )
                if profile
                else 0
            )


            seat_samples = (
                profile.get(
                    "seat_samples",
                    0,
                )
                if profile
                else 0
            )


            travel_samples = (
                travel.get(
                    "sample_count",
                    0,
                )
            )


            limited = bool(
                confidence
                == CONFIDENCE_LOW
            )


            # ------------------------------------------------
            # 안전 등급
            # ------------------------------------------------

            if (
                risk <= 0.1
                and margin >= 15
                and confidence
                != CONFIDENCE_LOW
            ):

                grade = (
                    "안전"
                )


            elif (
                risk <= 0.3
                and margin >= 5
            ):

                grade = (
                    "보통"
                )


            else:

                grade = (
                    "주의"
                )


            # ------------------------------------------------
            # 추천 근거
            # ------------------------------------------------

            reasons = []


            reasons.append(
                f"배차: "
                f'{boarding["headway_source"]}'
            )


            if (
                boarding[
                    "seat_source"
                ]
            ):

                reasons.append(
                    "좌석: "
                    f'{boarding["seat_source"]}'
                )


            if (
                live_info
                and not live_info.get(
                    "stale"
                )
            ):

                reasons.append(
                    "실시간 버스 도착정보를 "
                    "추천 계산에 반영했습니다."
                )


            elif profile:

                reasons.append(
                    "실시간 정보 대신 "
                    "과거 배차·좌석 기록을 사용했습니다."
                )


            else:

                reasons.append(
                    "배차·좌석 자료가 부족해 "
                    "노선 기본값을 사용했습니다."
                )


            if travel.get(
                "fallback"
            ):

                reasons.append(
                    "해당 시간대 이동기록이 부족해 "
                    "노선 기본 이동시간을 사용했습니다."
                )

            else:

                reasons.append(
                    f'이동시간은 '
                    f'{travel.get("basis", "과거 기록")} '
                    f'{travel_samples}건을 '
                    "기반으로 계산했습니다."
                )


            if (
                adjusted[
                    "highway_ml_applied"
                ]
            ):

                prediction = (
                    adjusted[
                        "highway_prediction"
                    ]
                )


                reasons.append(
                    "Highway ML을 적용했습니다. "
                    f'예상 속도 '
                    f'{prediction["predicted_speed"]:.1f} km/h, '
                    f'고속도로 예상시간 '
                    f'{prediction["highway_minutes"]:.1f}분.'
                )


            elif (
                adjusted[
                    "rain_fallback_applied"
                ]
            ):

                reasons.append(
                    "Highway ML 입력이 부족해 "
                    "강수 기반 지연 보정을 사용했습니다."
                )


            if (
                predicted_congestion
                is not None
            ):

                reasons.append(
                    "Congestion ML 예상 혼잡도 "
                    f"{predicted_congestion:.1f}"
                )


            if confidence == (
                CONFIDENCE_LOW
            ):

                reasons.append(
                    "일부 데이터가 부족해 "
                    "낮은 신뢰도의 fallback 추천입니다."
                )


            # =================================================
            # 후보 생성
            # =================================================

            candidate = {

                "route":
                    route,

                "boarding_station":
                    station[
                        "name"
                    ],

                "destination":
                    dest[
                        "name"
                    ],

                # 사용자가 정류장에 도착해야 하는 시각
                "station_ready_time":
                    slot.strftime(
                        "%H:%M"
                    ),

                # 버스 탑승 예상시각
                "departure_time":
                    departure.strftime(
                        "%H:%M"
                    ),

                # 목적지 도착 예상시각
                "estimated_arrival_time":
                    arrival.strftime(
                        "%H:%M"
                    ),

                "arrival_datetime":
                    arrival.isoformat(),

                # 대기시간
                "arrival_seconds":
                    round(
                        (
                            wait
                            + missed
                        )
                        * 60
                    ),

                # 좌석
                "remain_seats":
                    remain_seats,

                "seat_statistic":
                    boarding[
                        "seat_source"
                    ],

                # 배차
                "headway_minutes":
                    round(
                        headway,
                        1,
                    ),

                "headway_source":
                    boarding[
                        "headway_source"
                    ],

                # 만석 추가 대기
                "missed_bus_delay_minutes":
                    round(
                        missed,
                        1,
                    ),

                # 총 이동시간
                "travel_time_minutes":
                    round(
                        adjusted[
                            "minutes"
                        ],
                        1,
                    ),

                # 도착 여유
                "margin_minutes":
                    round(
                        margin,
                        1,
                    ),

                # 안전도
                "stability_grade":
                    grade,

                # 신뢰도
                "confidence":
                    confidence,

                "deadline_met":
                    True,

                # 데이터 출처
                "source":
                    (
                        "실시간 + 과거 + ML 통합 추천"
                        if live_info
                        and not live_info.get(
                            "stale"
                        )
                        else
                        "과거 + ML 기반 추천"
                    ),

                # 과거 혼잡도
                "historical_congestion":
                    historical_congestion,

                # ML 혼잡도
                "predicted_congestion":
                    predicted_congestion,

                # 표본
                "profile_samples":
                    seat_samples,

                "history_days":
                    profile_days,

                # 구간 이동시간
                "segment_minutes": {

                    "local_before":
                        round(
                            adjusted[
                                "pre_minutes"
                            ],
                            1,
                        ),

                    "giheung_sinnonhyeon":
                        round(
                            adjusted[
                                "core_minutes"
                            ],
                            1,
                        ),

                    "local_after":
                        round(
                            adjusted[
                                "after_minutes"
                            ],
                            1,
                        ),
                },

                # Highway ML
                "highway_ml_applied":
                    adjusted[
                        "highway_ml_applied"
                    ],

                "highway_prediction":
                    adjusted[
                        "highway_prediction"
                    ],

                "traffic_weight":
                    round(
                        adjusted[
                            "traffic_weight"
                        ],
                        3,
                    ),

                # fallback 여부
                "travel_fallback":
                    travel.get(
                        "fallback",
                        False,
                    ),

                # 추천 설명
                "reasons":
                    reasons,

                # 내부 정렬
                "_risk":
                    risk,

                "_limited":
                    limited,
            }


            candidates.append(
                candidate
            )


            slot += timedelta(
                minutes=10
            )


    # ========================================================
    # 후보 정렬
    # ========================================================

    risk_mode = (
        payload.get(
            "risk_mode",
            "safe",
        )
    )


    # --------------------------------------------------------
    # 출발시간 직접 입력
    # --------------------------------------------------------

    if not departure_flexible:


        # 시간 우선
        if risk_mode == "fast":

            candidates.sort(
                key=lambda c: (

                    c[
                        "arrival_datetime"
                    ],

                    c[
                        "_risk"
                    ],

                    safe_congestion_value(
                        c
                    ),
                )
            )


        # 안정 우선
        else:

            candidates.sort(
                key=lambda c: (

                    c[
                        "_limited"
                    ],

                    c[
                        "_risk"
                    ],

                    safe_congestion_value(
                        c
                    ),

                    -c[
                        "margin_minutes"
                    ],
                )
            )


    # --------------------------------------------------------
    # 출발시간 추천받기
    # --------------------------------------------------------

    else:


        # ====================================================
        # 시간 우선
        #
        # 마감 전에 도착할 수 있는 후보 중
        # 가능한 한 늦게 출발
        # ====================================================

        if risk_mode == "fast":

            candidates.sort(
                key=lambda c: (

                    -time_to_minutes(
                        c[
                            "station_ready_time"
                        ]
                    ),

                    c[
                        "_risk"
                    ],

                    safe_congestion_value(
                        c
                    ),
                )
            )


        # ====================================================
        # 안정 우선
        #
        # 15분 이상 여유가 있는 후보를 우선 남기고
        # 그 안에서 안정성과 출발시간을 함께 비교
        # ====================================================

        else:

            safe_candidates = [

                candidate

                for candidate
                in candidates

                if candidate[
                    "margin_minutes"
                ]
                >= SAFE_MARGIN_MINUTES
            ]


            # 안전 여유를 만족하는 후보가 있으면
            # 그것들만 대상으로 정렬
            if safe_candidates:

                candidates = (
                    safe_candidates
                )


            candidates.sort(
                key=lambda c: (

                    c[
                        "_limited"
                    ],

                    c[
                        "_risk"
                    ],

                    safe_congestion_value(
                        c
                    ),

                    -time_to_minutes(
                        c[
                            "station_ready_time"
                        ]
                    ),
                )
            )


    # ========================================================
    # 최대 4개 후보 선택
    # ========================================================

    chosen = []

    combinations = set()


    # 우선 서로 다른 노선/정류장
    for candidate in candidates:

        key = (
            candidate[
                "route"
            ],
            candidate[
                "boarding_station"
            ],
        )


        if key not in combinations:

            chosen.append(
                candidate
            )

            combinations.add(
                key
            )


        if len(
            chosen
        ) == 4:

            break


    # 부족하면 다른 시간 후보로 채움
    for candidate in candidates:

        if len(
            chosen
        ) == 4:

            break


        if candidate not in chosen:

            chosen.append(
                candidate
            )


    # ========================================================
    # rank
    # ========================================================

    for rank, candidate in enumerate(
        chosen,
        start=1,
    ):

        candidate[
            "rank"
        ] = rank

        candidate[
            "id"
        ] = (
            f"candidate-{rank}"
        )


        candidate.pop(
            "_risk",
            None,
        )

        candidate.pop(
            "_limited",
            None,
        )


    # ========================================================
    # 상태
    # ========================================================

    if chosen:

        status = "ok"

        message = (
            "조건에 맞는 추천 경로를 계산했습니다."
        )


    elif considered > 0:

        status = (
            "no_feasible_route"
        )

        message = (
            "도착 마감시간을 만족하는 "
            "경로가 없습니다."
        )


    else:

        status = (
            "insufficient_data"
        )

        message = (
            "추천 가능한 경로를 찾지 못했습니다."
        )


    # ========================================================
    # 결과
    # ========================================================

    return {

        "status":
            status,

        "message":
            message,

        "candidates":
            chosen,

        "recommended":
            (
                chosen[0]
                if chosen
                else None
            ),

        "missing_data":
            sorted(
                missing
            ),

        "coverage_complete":
            len(
                missing
            )
            == 0,

        "considered":
            considered,

        "late_candidates":
            late_count,

        "request":
            payload,

        "departure_flexible":
            departure_flexible,

        "station_evidence":
            station_evidence(
                store,
                selected,
                start,
            ),

        "local_section_evidence": {
            route:
                store.workbooks.sections(
                    route,
                    start,
                )
            for route, _
            in seen
        },

        "model_note": (
            "Highway ML은 평일 오전의 "
            "고속도로 속도를 예측하고, "
            "Congestion ML은 정류장별 "
            "예상 혼잡도를 예측합니다. "
            "실시간 데이터가 있으면 ETA·좌석·배차를 "
            "우선 반영하며, 부족한 경우 "
            "과거 자료와 fallback을 사용합니다."
        ),
    }


# ============================================================
# 정류장 근거
# ============================================================

def station_evidence(
    store,
    selections,
    target,
):

    evidence = []

    seen = set()


    for selection in selections:

        route = (
            selection.get(
                "route_name"
            )
        )


        if route not in store.catalog:

            continue


        stop = next(
            (
                station
                for station
                in store.catalog[
                    route
                ][
                    "boarding"
                ]
                if str(
                    station[
                        "id"
                    ]
                )
                == str(
                    selection.get(
                        "id"
                    )
                )
            ),
            None,
        )


        if not stop:

            continue


        key = (
            route,
            stop[
                "id"
            ],
        )


        if key in seen:

            continue


        seen.add(
            key
        )


        try:

            profile = (
                store.profile(
                    route,
                    stop[
                        "realtime_id"
                    ],
                    target,
                )
            )

        except Exception:

            profile = None


        try:

            congestion = (
                store.congestion(
                    route,
                    stop.get(
                        "historical_id"
                    ),
                    target,
                )
            )

        except Exception:

            congestion = None


        predicted_congestion = (
            predict_candidate_congestion(
                route=route,
                station=stop,
                departure=target,
            )
        )


        evidence.append({

            "route":
                route,

            "station":
                stop[
                    "name"
                ],

            "hour":
                target.hour,

            "profile":
                profile,

            "congestion":
                congestion,

            "predicted_congestion":
                predicted_congestion,
        })


    return evidence