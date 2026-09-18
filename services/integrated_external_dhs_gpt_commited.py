"""
External API integration for BusFlow.

역할
----
1. .env 로드
2. Open-Meteo 날씨 조회
3. 경기 버스 실시간 도착정보 조회
4. API 실패 시 realtime.db fallback

환경변수
--------
DATA_API_KEY
    경기 버스 공공데이터 API 키

BUSFLOW_OFFLINE=1
    외부 API 호출 금지
"""

from __future__ import annotations

import json
import os
import time

from datetime import (
    datetime,
    timedelta,
    timezone,
)

from pathlib import Path

from urllib.parse import (
    urlencode,
    unquote,
)

from urllib.request import urlopen

from services.integrated_data_dhs_gpt_commited import (
    ROOT,
    number,
)


# ============================================================
# 시간대
# ============================================================

KST = timezone(
    timedelta(
        hours=9
    )
)


def now_kst():
    """
    timezone 정보가 제거된
    한국 현재시각을 반환한다.
    """

    return (
        datetime.now(KST)
        .replace(
            tzinfo=None
        )
    )


# ============================================================
# .env
# ============================================================

def load_env(
    root=ROOT,
):

    path = (
        Path(root)
        / ".env"
    )

    if not path.is_file():
        return

    for line in path.read_text(
        encoding="utf-8-sig"
    ).splitlines():

        stripped = line.strip()

        if (
            not stripped
            or stripped.startswith("#")
        ):
            continue

        key, separator, value = (
            stripped.partition("=")
        )

        if (
            not separator
            or not key
        ):
            continue

        os.environ.setdefault(
            key.strip(),
            value
            .strip()
            .strip('"')
            .strip("'"),
        )


# ============================================================
# 공통 JSON 요청
# ============================================================

def request_json(
    url,
    params,
):

    if (
        os.getenv(
            "BUSFLOW_OFFLINE"
        )
        == "1"
    ):

        raise RuntimeError(
            "외부 API 검증 보류: "
            "오프라인 모드"
        )

    try:

        full_url = (
            url
            + "?"
            + urlencode(
                params
            )
        )

        with urlopen(
            full_url,
            timeout=12,
        ) as response:

            return json.load(
                response
            )

    except Exception:

        # API 키 또는 요청 URL이
        # 에러에 노출되지 않도록
        # 원본 exception은 숨김
        raise RuntimeError(
            "외부 서비스 연결 실패. "
            "잠시 후 다시 조회해주세요."
        ) from None


# ============================================================
# External Service
# ============================================================

class External:

    # 대표 기상지점
    REGIONS = {

        "giheung": (
            "기흥구",
            37.2803,
            127.1146,
        ),

        "gangnam": (
            "강남구",
            37.5172,
            127.0473,
        ),
    }


    def __init__(
        self,
        store,
    ):

        self.store = store

        self.cache = {}

        load_env(
            store.root
        )


    # ========================================================
    # 날씨
    # ========================================================

    def weather(
        self,
        target,
        region="giheung",
    ):
        """
        Open-Meteo 시간별 예보 조회.

        반환값:
        - temperature
        - humidity
        - wind_speed
        - rainfall_mm_per_hour
        - condition

        Highway ML 입력값으로 사용된다.
        """

        # ----------------------------------------------------
        # 지역 검증
        # ----------------------------------------------------

        if (
            region
            not in self.REGIONS
        ):

            raise ValueError(
                "지원하지 않는 날씨 지역"
            )


        now = now_kst()

        day_delta = (
            target.date()
            - now.date()
        ).days


        # ----------------------------------------------------
        # Open-Meteo 예보 범위
        # ----------------------------------------------------

        if not (
            0
            <= day_delta
            <= 6
        ):

            return {

                "region":
                    region,

                "label":
                    self.REGIONS[
                        region
                    ][0],

                "available":
                    False,

                "message":
                    (
                        "예보 지원 범위는 "
                        "오늘부터 6일 뒤까지입니다."
                    ),

                "target_time":
                    target.isoformat(),
            }


        # ----------------------------------------------------
        # 캐시 키
        # ----------------------------------------------------

        cache_key = (
            "weather",
            region,
        )

        cached = (
            self.cache.get(
                cache_key
            )
        )


        try:

            # ------------------------------------------------
            # 최근 실패 시 60초 재시도 방지
            # ------------------------------------------------

            failed_at = (
                self.cache.get(
                    (
                        "weather_failure",
                        region,
                    )
                )
            )

            if (
                failed_at
                and (
                    time.monotonic()
                    - failed_at
                )
                < 60
            ):

                raise RuntimeError(
                    "날씨 서비스 재시도 대기"
                )


            # ------------------------------------------------
            # 10분 캐시
            # ------------------------------------------------

            if (
                cached
                and (
                    time.monotonic()
                    - cached[0]
                )
                < 600
            ):

                data = (
                    cached[1]
                )

                cache_age = (
                    time.monotonic()
                    - cached[0]
                )

            else:

                (
                    label,
                    latitude,
                    longitude,
                ) = (
                    self.REGIONS[
                        region
                    ]
                )


                data = request_json(
                    "https://api.open-meteo.com/v1/forecast",
                    {
                        "latitude":
                            latitude,

                        "longitude":
                            longitude,

                        "hourly": (
                            "temperature_2m,"
                            "relative_humidity_2m,"
                            "wind_speed_10m,"
                            "rain,"
                            "showers,"
                            "weather_code"
                            
                        ),

                        "timezone":
                            "Asia/Seoul",

                        "wind_speed_unit":
                            "ms",

                        "forecast_days":
                            7,
                    },
                )


                self.cache[
                    cache_key
                ] = (
                    time.monotonic(),
                    data,
                )

                cache_age = 0


            # ------------------------------------------------
            # 시간별 데이터
            # ------------------------------------------------

            hourly = (
                data[
                    "hourly"
                ]
            )


            target_hour = (
                target.replace(
                    minute=0,
                    second=0,
                    microsecond=0,
                )
            )


            stamp = (
                target_hour
                .isoformat(
                    timespec="minutes"
                )
            )


            index = (
                hourly[
                    "time"
                ]
                .index(
                    stamp
                )
            )


            # ------------------------------------------------
            # 강수량
            #
            # Open-Meteo rain/showers는
            # 이전 1시간 누적값이므로
            # 07시 선택 시 08시 timestamp 사용
            # ------------------------------------------------

            rain_index = (
                index
                + 1
            )


            large_scale_rain = number(
                hourly[
                    "rain"
                ][
                    rain_index
                ]
            )


            showers = number(
                hourly[
                    "showers"
                ][
                    rain_index
                ]
            )


            if (
                large_scale_rain
                is None
                or showers
                is None
            ):

                raise ValueError(
                    "missing rainfall"
                )


            rainfall = (
                large_scale_rain
                + showers
            )


            # ------------------------------------------------
            # Highway ML 입력값
            # ------------------------------------------------

            temperature = number(
                hourly[
                    "temperature_2m"
                ][
                    index
                ]
            )


            humidity = number(
                hourly[
                    "relative_humidity_2m"
                ][
                    index
                ]
            )


            wind_speed = number(
                hourly[
                    "wind_speed_10m"
                ][
                    index
                ]
            )


            if (
                temperature
                is None
                or humidity
                is None
                or wind_speed
                is None
            ):

                raise ValueError(
                    "missing weather feature"
                )


            # ------------------------------------------------
            # 날씨 상태
            # ------------------------------------------------

            weather_code = int(
                hourly[
                    "weather_code"
                ][
                    index
                ]
            )


            if (
                weather_code
                == 0
            ):

                condition = (
                    "맑음"
                )

            elif weather_code in (
                1,
                2,
                3,
            ):

                condition = (
                    "구름"
                )

            elif weather_code in (
                45,
                48,
            ):

                condition = (
                    "안개"
                )

            elif weather_code in (
                71,
                73,
                75,
                77,
                85,
                86,
            ):

                condition = (
                    "눈"
                )

            else:

                condition = (
                    "비"
                )


            # ------------------------------------------------
            # 성공 응답
            # ------------------------------------------------

            return {

                "region":
                    region,

                "label":
                    self.REGIONS[
                        region
                    ][0],

                "available":
                    True,

                "temperature":
                    temperature,

                "humidity":
                    humidity,

                "wind_speed":
                    wind_speed,

                "rainfall_mm_per_hour":
                    rainfall,

                "condition":
                    condition,

                "source":
                    (
                        "Open-Meteo "
                        "시간별 예보"
                    ),

                "target_time":
                    stamp,

                "representative_point":
                    True,

                "retrieved_at":
                    (
                        now
                        - timedelta(
                            seconds=(
                                cache_age
                            )
                        )
                    )
                    .isoformat(
                        timespec="seconds"
                    ),
            }


        except (
            RuntimeError,
            KeyError,
            ValueError,
            IndexError,
            TypeError,
        ):

            self.cache[
                (
                    "weather_failure",
                    region,
                )
            ] = (
                time.monotonic()
            )


            return {

                "region":
                    region,

                "label":
                    self.REGIONS[
                        region
                    ][0],

                "available":
                    False,

                "message":
                    (
                        "날씨 정보를 "
                        "불러오지 못했습니다."
                    ),

                "target_time":
                    target.isoformat(),
            }


    # ========================================================
    # 실시간 버스 정보
    # ========================================================

    def live(
        self,
        route,
        station_id,
    ):
        """
        경기 버스 도착정보 API 조회.

        최대 2대:
        - arrival_seconds
        - remain_seats
        - vehicle_id

        두 차량이 있으면:
        - headway_minutes

        API 실패 시 realtime.db fallback.
        """

        # ----------------------------------------------------
        # 노선 / 정류장 검증
        # ----------------------------------------------------

        spec = (
            self.store.catalog.get(
                route
            )
        )


        if not spec:

            raise ValueError(
                "지원하지 않는 노선"
            )


        valid_station_ids = {

            station[
                "realtime_id"
            ]

            for station
            in spec[
                "boarding"
            ]
        }


        if (
            station_id
            not in valid_station_ids
        ):

            raise ValueError(
                "지원하지 않는 "
                "노선·정류장 조합"
            )


        # ----------------------------------------------------
        # 60초 API 캐시
        # ----------------------------------------------------

        cache_key = (
            "live",
            route,
            station_id,
        )


        cached = (
            self.cache.get(
                cache_key
            )
        )


        age = None


        try:

            if (
                cached
                and (
                    time.monotonic()
                    - cached[0]
                )
                < 60
            ):

                arrivals = (
                    cached[1]
                )

                age = (
                    time.monotonic()
                    - cached[0]
                )


            else:

                token = (
                    os.getenv(
                        "DATA_API_KEY",
                        "",
                    )
                    .strip()
                )


                if not token:

                    raise RuntimeError(
                        "DATA_API_KEY "
                        "환경변수가 필요합니다."
                    )


                data = request_json(
                    (
                        "https://apis.data.go.kr/"
                        "6410000/"
                        "busarrivalservice/v2/"
                        "getBusArrivalListv2"
                    ),
                    {
                        "serviceKey":
                            unquote(
                                token
                            ),

                        "stationId":
                            station_id,

                        "format":
                            "json",
                    },
                )


                response = (
                    data[
                        "response"
                    ]
                )


                header = (
                    response[
                        "msgHeader"
                    ]
                )


                result_code = str(
                    header.get(
                        "resultCode"
                    )
                )


                if (
                    result_code
                    not in (
                        "0",
                        "4",
                    )
                ):

                    raise RuntimeError(
                        "버스 API 응답 오류"
                    )


                arrivals = (
                    (
                        response.get(
                            "msgBody"
                        )
                        or {}
                    )
                    .get(
                        "busArrivalList"
                    )
                    or []
                )


                if isinstance(
                    arrivals,
                    dict,
                ):

                    arrivals = [
                        arrivals
                    ]


                self.cache[
                    cache_key
                ] = (
                    time.monotonic(),
                    arrivals,
                )


                age = 0


            # ------------------------------------------------
            # 해당 노선만 필터
            # ------------------------------------------------

            targets = [

                row

                for row
                in arrivals

                if str(
                    row.get(
                        "routeId"
                    )
                )
                == str(
                    spec[
                        "route_id"
                    ]
                )
            ]


            # ------------------------------------------------
            # 동일 정류장에
            # 노선 방향이 여러 개 잡히는 경우 방지
            # ------------------------------------------------

            orders = {

                str(
                    row.get(
                        "staOrder"
                    )
                )

                for row
                in targets
            }


            if len(
                orders
            ) > 1:

                raise RuntimeError(
                    "같은 정류장의 "
                    "운행 방향을 "
                    "추가 확인해야 합니다."
                )


            bus = (
                targets[0]
                if targets
                else {}
            )


            # ------------------------------------------------
            # 실시간 결과
            # ------------------------------------------------

            result = {

                "route":
                    route,

                "station_id":
                    station_id,

                "stale":
                    False,

                "source":
                    (
                        "실시간 API"
                        if age == 0
                        else "최근 API 캐시"
                    ),

                "collected_at":
                    (
                        now_kst()
                        - timedelta(
                            seconds=(
                                age
                            )
                        )
                    )
                    .isoformat(
                        timespec="seconds"
                    ),

                "age_seconds":
                    int(
                        age
                    ),

                "buses":
                    [],
            }


            # ------------------------------------------------
            # 첫 번째 / 두 번째 버스
            # ------------------------------------------------

            for number_index in (
                1,
                2,
            ):

                eta = number(
                    bus.get(
                        f"predictTimeSec"
                        f"{number_index}"
                    )
                )


                # 초 단위가 없으면
                # 분 단위 fallback
                if eta is None:

                    minutes = number(
                        bus.get(
                            f"predictTime"
                            f"{number_index}"
                        )
                    )


                    if (
                        minutes
                        is not None
                    ):

                        eta = (
                            minutes
                            * 60
                        )


                if eta is None:

                    continue


                result[
                    "buses"
                ].append({

                    "arrival_seconds":
                        max(
                            0,
                            eta
                            - age,
                        ),

                    "remain_seats":
                        number(
                            bus.get(
                                f"remainSeatCnt"
                                f"{number_index}"
                            )
                        ),

                    "vehicle_id":
                        bus.get(
                            f"vehId"
                            f"{number_index}"
                        ),
                })


            # ------------------------------------------------
            # 실시간 배차간격
            # ------------------------------------------------

            if (
                len(
                    result[
                        "buses"
                    ]
                )
                == 2
            ):

                result[
                    "headway_minutes"
                ] = (

                    result[
                        "buses"
                    ][1][
                        "arrival_seconds"
                    ]

                    - result[
                        "buses"
                    ][0][
                        "arrival_seconds"
                    ]

                ) / 60


            else:

                result[
                    "headway_minutes"
                ] = None


            return result


        # ====================================================
        # API 실패 → DB fallback
        # ====================================================

        except (
            RuntimeError,
            KeyError,
            ValueError,
            TypeError,
        ):

            rows = []


            for table in (
                "realtime_arrival_a",
                "realtime_arrival_b",
                "realtime_arrival",
            ):

                table_rows = (
                    self.store.read(
                        "realtime.db",
                        table,
                    )
                )


                rows.extend(

                    row

                    for row
                    in table_rows

                    if (
                        row.get(
                            "route_name"
                        )
                        == route
                        and str(
                            row.get(
                                "station_id"
                            )
                        )
                        == str(
                            station_id
                        )
                    )
                )


            # ------------------------------------------------
            # 마지막 저장 데이터 사용
            # ------------------------------------------------

            if rows:

                row = max(
                    rows,
                    key=lambda item:
                    item.get(
                        "collected_at",
                        "",
                    ),
                )


                buses = []


                for number_index in (
                    1,
                    2,
                ):

                    arrival_seconds = number(
                        row.get(
                            f"predict_time_sec_"
                            f"{number_index}"
                        )
                    )


                    remain_seats = number(
                        row.get(
                            f"remain_seat_cnt_"
                            f"{number_index}"
                        )
                    )


                    if (
                        arrival_seconds
                        is None
                        and remain_seats
                        is None
                    ):

                        continue


                    buses.append({

                        "arrival_seconds":
                            arrival_seconds,

                        "remain_seats":
                            remain_seats,
                    })


                return {

                    "route":
                        route,

                    "station_id":
                        station_id,

                    "stale":
                        True,

                    "source":
                        (
                            "과거 수집 기록 · "
                            "현재 도착정보 아님"
                        ),

                    "collected_at":
                        row.get(
                            "collected_at"
                        ),

                    "headway_minutes":
                        None,

                    "buses":
                        buses,
                }


            # ------------------------------------------------
            # fallback 데이터도 없음
            # ------------------------------------------------

            return {

                "route":
                    route,

                "station_id":
                    station_id,

                "stale":
                    True,

                "source":
                    (
                        "조회 실패 또는 "
                        "API 설정 필요"
                    ),

                "buses":
                    [],

                "headway_minutes":
                    None,
            }