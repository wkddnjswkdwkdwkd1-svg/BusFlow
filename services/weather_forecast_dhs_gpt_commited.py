from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote

import requests
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEATHER_DB_PATH = PROJECT_ROOT / "data" / "weather.db"

URL = (
    "https://apis.data.go.kr/1360000/"
    "VilageFcstInfoService_2.0/getVilageFcst"
)

KMA_API_KEY = unquote(
    (
        os.getenv("KMA_API_KEY")
        or os.getenv("KMA_SERVICE_KEY")
        or ""
    ).strip()
)

# 기상청 단기예보 격자
GRID_BY_REGION = {
    "yongin": (60, 121),
    "suwon": (60, 121),
    "seoul": (60, 127),
}

BASE_TIMES = [2, 5, 8, 11, 14, 17, 20, 23]


def _latest_base_datetime(now: datetime) -> datetime:
    # 단기예보 발표 후 API 반영 시간을 조금 기다린다.
    candidates = []

    day = now.replace(
        minute=0,
        second=0,
        microsecond=0,
    )

    for hour in BASE_TIMES:
        candidates.append(
            day.replace(hour=hour)
            + timedelta(minutes=15)
        )

    usable = [
        item
        for item in candidates
        if item <= now
    ]

    if usable:
        return max(usable).replace(minute=0)

    yesterday = day - timedelta(days=1)
    return yesterday.replace(hour=23)


def _parse_precipitation(value) -> float:
    if value is None:
        return 0.0

    text = str(value).strip()

    if (
        not text
        or "강수없음" in text
        or text in {"0", "0.0", "-"}
    ):
        return 0.0

    numbers = [
        float(x)
        for x in re.findall(r"[\d.]+", text)
    ]

    if not numbers:
        return 0.0

    if "미만" in text:
        return numbers[0] / 2.0

    if "~" in text and len(numbers) >= 2:
        return (numbers[0] + numbers[1]) / 2.0

    # "30.0mm 이상"은 하한값을 사용한다.
    return numbers[0]


def _condition_from(sky, pty) -> str:
    pty = str(pty or "0")

    if pty in {"1", "4"}:
        return "비"
    if pty in {"2", "3"}:
        return "눈/비"
    if str(sky) == "4":
        return "흐림"
    if str(sky) == "3":
        return "구름많음"

    return "맑음"


def _emoji_from(condition: str) -> str:
    if "눈" in condition:
        return "🌨️"
    if "비" in condition:
        return "🌧️"
    if "흐림" in condition:
        return "☁️"
    if "구름" in condition:
        return "🌤️"
    return "☀️"


def _float(values: dict, key: str):
    try:
        return float(values.get(key))
    except (TypeError, ValueError):
        return None


def fetch_forecast(
    target_datetime: datetime | None = None,
    region: str = "yongin",
) -> dict:
    if not KMA_API_KEY:
        raise RuntimeError(
            "KMA_API_KEY 환경변수가 없습니다."
        )

    target = target_datetime or datetime.now()
    now = datetime.now()

    if target < now - timedelta(hours=1):
        raise RuntimeError(
            "과거 시각은 단기예보 API 대신 "
            "weather.db 관측값을 사용해야 합니다."
        )

    nx, ny = GRID_BY_REGION.get(
        region,
        GRID_BY_REGION["yongin"],
    )

    base_dt = _latest_base_datetime(now)

    params = {
        "serviceKey": KMA_API_KEY,
        "pageNo": 1,
        "numOfRows": 1000,
        "dataType": "JSON",
        "base_date": base_dt.strftime("%Y%m%d"),
        "base_time": base_dt.strftime("%H00"),
        "nx": nx,
        "ny": ny,
    }

    response = requests.get(
        URL,
        params=params,
        timeout=12,
    )
    response.raise_for_status()

    data = response.json()

    header = data["response"]["header"]

    if header.get("resultCode") != "00":
        raise RuntimeError(
            "KMA forecast error: "
            f"{header.get('resultCode')} "
            f"{header.get('resultMsg')}"
        )

    items = (
        data["response"]["body"]
        ["items"]["item"]
    )

    grouped = {}

    for item in items:
        key = (
            str(item.get("fcstDate")),
            str(item.get("fcstTime")),
        )

        grouped.setdefault(
            key,
            {},
        )[item.get("category")] = (
            item.get("fcstValue")
        )

    wanted = (
        target.strftime("%Y%m%d"),
        target.strftime("%H00"),
    )

    values = grouped.get(wanted)
    selected_dt = target.replace(
        minute=0,
        second=0,
        microsecond=0,
    )

    if values is None:
        options = []

        for (date_text, time_text), item in grouped.items():
            try:
                dt = datetime.strptime(
                    date_text + time_text,
                    "%Y%m%d%H%M",
                )
            except ValueError:
                continue

            options.append(
                (
                    abs(
                        (
                            dt - target
                        ).total_seconds()
                    ),
                    dt,
                    item,
                )
            )

        if not options:
            raise RuntimeError(
                "KMA 예보 데이터가 비어 있습니다."
            )

        _, selected_dt, values = min(
            options,
            key=lambda x: x[0],
        )

    rainfall = _parse_precipitation(
        values.get("PCP")
    )

    condition = _condition_from(
        values.get("SKY"),
        values.get("PTY"),
    )

    return {
        "forecast_datetime": (
            selected_dt.strftime(
                "%Y-%m-%d %H:%M"
            )
        ),
        "temperature": _float(values, "TMP"),
        "humidity": _float(values, "REH"),
        "wind_speed": _float(values, "WSD"),
        "rainfall": rainfall,
        "precipitation": rainfall,
        "condition": condition,
        "emoji": _emoji_from(condition),
        "precipitation_probability": (
            _float(values, "POP")
        ),
        "source": "KMA_VilageFcst",
        "region": region,
        "grid": {
            "nx": nx,
            "ny": ny,
        },
    }


def load_historical_weather(
    target_datetime: datetime,
) -> dict:
    if not WEATHER_DB_PATH.exists():
        raise RuntimeError(
            f"weather.db 없음: {WEATHER_DB_PATH}"
        )

    hour = int(target_datetime.hour)

    # DB는 06~07 형태로 저장되어 있다.
    time_zone = (
        f"{hour:02d}~{hour + 1:02d}"
    )

    date_key = target_datetime.strftime(
        "%Y%m%d"
    )

    connection = sqlite3.connect(
        WEATHER_DB_PATH
    )

    try:
        row = connection.execute(
            """
            SELECT
                AVG(temperature),
                AVG(COALESCE(rainfall, 0)),
                AVG(humidity),
                AVG(wind_speed),
                COUNT(*)
            FROM weather
            WHERE REPLACE(opr_ymd, '-', '') = ?
              AND time_zone = ?
            """,
            (
                date_key,
                time_zone,
            ),
        ).fetchone()
    finally:
        connection.close()

    if (
        row is None
        or int(row[4] or 0) == 0
    ):
        raise RuntimeError(
            "해당 날짜/시간대의 "
            "weather.db 관측값이 없습니다."
        )

    temperature = row[0]
    rainfall = float(row[1] or 0.0)
    humidity = row[2]
    wind_speed = row[3]

    condition = (
        "비"
        if rainfall > 0
        else "과거 관측"
    )

    return {
        "forecast_datetime": (
            target_datetime.strftime(
                "%Y-%m-%d %H:00"
            )
        ),
        "temperature": temperature,
        "humidity": humidity,
        "wind_speed": wind_speed,
        "rainfall": rainfall,
        "precipitation": rainfall,
        "condition": condition,
        "emoji": _emoji_from(condition),
        "precipitation_probability": None,
        "source": "weather_db_historical",
        "region": "historical",
        "sample_count": int(row[4] or 0),
    }


def get_weather_for_datetime(
    target_datetime: datetime,
    region: str = "yongin",
) -> dict:
    """
    미래/현재는 기상청 단기예보,
    과거 replay는 weather.db를 사용한다.
    """
    now = datetime.now()

    if target_datetime < now - timedelta(hours=1):
        return load_historical_weather(
            target_datetime
        )

    try:
        return fetch_forecast(
            target_datetime,
            region=region,
        )
    except Exception as forecast_error:
        # 같은 날짜가 weather.db에 이미 있으면 관측값으로 fallback.
        try:
            return load_historical_weather(
                target_datetime
            )
        except Exception as db_error:
            raise RuntimeError(
                "날씨 데이터를 불러오지 못했습니다. "
                f"forecast={forecast_error}; "
                f"db={db_error}"
            ) from forecast_error
