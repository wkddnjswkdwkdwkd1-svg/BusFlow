from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from pathlib import Path

from flask import Response, jsonify, request

import app as friend_app

from services.highway_predictor_dhs_gpt_commited import (
    predict_highway_time as dhs_predict_highway_time,
)
from services.recommendation_postprocess_dhs_gpt_commited import (
    apply_weather_to_candidates,
    build_reasons,
    select_by_route_and_risk,
)
from services.weather_delay_service_dhs_gpt_commited import (
    get_weather_delay_summary,
    is_public_holiday_kr,
    predict_highway_weather_adjusted,
)
from services.weather_forecast_dhs_gpt_commited import (
    get_weather_for_datetime,
)


app = friend_app.app
PROJECT_ROOT = Path(__file__).resolve().parent

_selected_date = ContextVar(
    "dhs_gpt_commited_selected_date",
    default=None,
)

_selected_is_holiday = ContextVar(
    "dhs_gpt_commited_selected_is_holiday",
    default=None,
)

_friend_predict_highway_time = (
    friend_app.predict_highway_time
)


def _dhs_predict_adapter(
    *args,
    **kwargs,
):
    hour = kwargs.get("hour")

    if hour is None and args:
        hour = args[0]

    hour = int(hour)

    # 기존 /api/recommend 호출은 친구 로직 그대로 유지.
    # /api/recommend-v2에서 ContextVar가 잡힌 경우에만
    # 새 모델을 사용한다.
    selected_date = _selected_date.get()

    if (
        selected_date is None
        or hour not in (6, 7, 8)
    ):
        return _friend_predict_highway_time(
            *args,
            **kwargs,
        )

    kwargs["target_date"] = (
        selected_date
    )

    kwargs[
        "is_public_holiday"
    ] = _selected_is_holiday.get()

    return dhs_predict_highway_time(
        *args,
        **kwargs,
    )


# 친구 파일을 디스크에서 수정하지 않고
# 이 overlay 프로세스 안에서만 호출 대상을 바꾼다.
friend_app.predict_highway_time = (
    _dhs_predict_adapter
)


def _inject_weather_notice_ui(
    html: str,
) -> str:
    """
    친구 HTML의 레이아웃/문구를 재설계하지 않고,
    원래 비어 있던 날씨 안내 영역과 결과 metric 영역만 채운다.
    원본 HTML 파일 자체는 수정하지 않는다.
    """
    old_chip = """<div class="weather-chip">
              추천 계산에 기상 조건을 반영할 수 있도록 연결 예정
            </div>"""

    new_chip = (
        '<div id="weatherDelayNotice" '
        'class="weather-chip" hidden></div>'
    )

    if old_chip in html:
        html = html.replace(
            old_chip,
            new_chip,
            1,
        )

    css_tag = (
        '<link rel="stylesheet" '
        'href="/dhs_gpt_commited/ui.css">'
    )

    js_tag = (
        '<script src="/dhs_gpt_commited/ui.js" '
        'defer></script>'
    )

    if css_tag not in html:
        html = html.replace(
            "</head>",
            f"  {css_tag}\n</head>",
            1,
        )

    if js_tag not in html:
        html = html.replace(
            "</body>",
            f"  {js_tag}\n</body>",
            1,
        )

    return html


@app.get("/dhs_gpt_commited/ui.js")
def ui_js_dhs_gpt_commited():
    return Response(
        (
            PROJECT_ROOT
            / "Time_Keeper_ui_dhs_gpt_commited.js"
        ).read_text(encoding="utf-8"),
        mimetype="application/javascript",
    )


@app.get("/dhs_gpt_commited/ui.css")
def ui_css_dhs_gpt_commited():
    return Response(
        (
            PROJECT_ROOT
            / "Time_Keeper_ui_dhs_gpt_commited.css"
        ).read_text(encoding="utf-8"),
        mimetype="text/css",
    )

def timekeeper_dhs_gpt_commited():
    source = (
        PROJECT_ROOT
        / "Time_Keeper_mid_prototype.html"
    ).read_text(
        encoding="utf-8"
    )

    return Response(
        _inject_weather_notice_ui(
            source
        ),
        mimetype="text/html",
    )


# 기존 /, /timekeeper 라우트는 그대로 존재한다.
# 이 overlay 실행 시에만 view function을 교체한다.
app.view_functions[
    "timekeeper"
] = timekeeper_dhs_gpt_commited


@app.get("/api/weather")
def weather_api_dhs_gpt_commited():
    target_text = request.args.get(
        "datetime"
    )

    region = request.args.get(
        "region",
        "yongin",
    )

    if target_text:
        try:
            target = datetime.fromisoformat(
                target_text
            )
        except ValueError:
            return jsonify({
                "error": "invalid_datetime"
            }), 400
    else:
        target = datetime.now()

    try:
        weather = (
            get_weather_for_datetime(
                target,
                region=region,
            )
        )

        delay = (
            get_weather_delay_summary(
                target.date(),
                weather.get(
                    "rainfall",
                    0.0,
                ),
            )
        )

        result = {
            **weather,
            **delay,
        }

        rainfall = float(
            result.get(
                "rainfall",
                0.0,
            )
            or 0.0
        )

        pop = result.get(
            "precipitation_probability"
        )

        condition = result.get(
            "condition",
            "현재 날씨",
        )

        detail = (
            f"{condition} · "
            f"강수 {rainfall:g} mm/h"
        )

        if pop is not None:
            detail += (
                f" · 강수확률 "
                f"{pop:.0f}%"
            )

        result["summary"] = detail

        return jsonify(result)

    except Exception as error:
        return jsonify({
            "error": (
                "weather_source_failed"
            ),
            "message": str(error),
            "temperature": None,
            "humidity": None,
            "wind_speed": None,
            "rainfall": 0.0,
            "precipitation": 0.0,
            "condition": "날씨 API 대기",
            "emoji": "🌙",
            "weather_delay_percent": 0.0,
            "weather_delay_weight": 1.0,
            "weather_notice": None,
            "summary": (
                "기상 데이터를 "
                "불러오지 못했습니다."
            ),
        }), 503


@app.get(
    "/api/dhs_gpt_commited/weather-delay"
)
def weather_delay_debug_dhs_gpt_commited():
    date_text = request.args.get(
        "date",
        datetime.now().strftime(
            "%Y-%m-%d"
        ),
    )

    hour = int(
        request.args.get(
            "hour",
            7,
        )
    )

    rainfall = float(
        request.args.get(
            "rainfall",
            0.0,
        )
    )

    try:
        result = (
            predict_highway_weather_adjusted(
                target_date=date_text,
                hour=hour,
                rainfall_mm_per_hour=rainfall,
                is_public_holiday=(
                    is_public_holiday_kr(
                        date_text
                    )
                ),
            )
        )
        return jsonify(result)

    except Exception as error:
        return jsonify({
            "error": str(error)
        }), 400


@app.get(
    "/api/dhs_gpt_commited/health"
)
def health_dhs_gpt_commited():
    checks = {
        "overlay": True,
        "friend_app_imported": True,
        "weather_db_exists": (
            PROJECT_ROOT
            .joinpath(
                "data",
                "weather.db",
            )
            .exists()
        ),
        "realtime_db_exists": (
            PROJECT_ROOT
            .joinpath(
                "data",
                "realtime.db",
            )
            .exists()
        ),
        "supported_highway_hours": [
            6,
            7,
            8,
        ],
    }

    return jsonify(checks)


@app.post("/api/recommend-v2")
def recommend_v2_dhs_gpt_commited():
    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    commute_mode = data.get(
        "commute_mode",
        "morning",
    )

    route_family = data.get(
        "route_family"
    )

    risk_mode = data.get(
        "risk_mode",
        "safe",
    )

    date_string = data.get(
        "date"
    )

    if not date_string:
        return jsonify({
            "error": (
                "date 값이 필요합니다."
            )
        }), 400

    departure_time = data.get(
        "departure_time"
    )

    arrival_time = data.get(
        "arrival_time"
    )

    if not departure_time:
        departure_time = (
            "07:00"
            if commute_mode == "morning"
            else "17:30"
        )

    if not arrival_time:
        arrival_time = (
            "08:50"
            if commute_mode == "morning"
            else "19:00"
        )

    try:
        target_dt = datetime.strptime(
            (
                f"{date_string} "
                f"{departure_time}"
            ),
            "%Y-%m-%d %H:%M",
        )
    except ValueError:
        return jsonify({
            "error": (
                "날짜/시간 형식이 "
                "올바르지 않습니다."
            )
        }), 400

    region = (
        "yongin"
        if commute_mode == "morning"
        else "seoul"
    )

    try:
        weather = (
            get_weather_for_datetime(
                target_dt,
                region=region,
            )
        )
    except Exception as error:
        weather = {
            "temperature": None,
            "humidity": None,
            "wind_speed": None,
            "rainfall": 0.0,
            "precipitation": 0.0,
            "source": "unavailable",
            "error": str(error),
        }

    is_supported_highway_time = (
        commute_mode == "morning"
        and target_dt.hour in (6, 7, 8)
    )

    # 친구 recommend()의 기존 gate가
    # temperature/humidity/wind_speed None 여부를 본다.
    # 새 모델은 이 변수를 feature로 쓰지 않으므로
    # 지원시간에는 0 fallback으로 gate만 통과시킨다.
    if is_supported_highway_time:
        model_weather = {
            "temperature": (
                weather.get(
                    "temperature"
                )
                if weather.get(
                    "temperature"
                )
                is not None
                else 0.0
            ),
            "humidity": (
                weather.get(
                    "humidity"
                )
                if weather.get(
                    "humidity"
                )
                is not None
                else 0.0
            ),
            "wind_speed": (
                weather.get(
                    "wind_speed"
                )
                if weather.get(
                    "wind_speed"
                )
                is not None
                else 0.0
            ),
            "rainfall": float(
                weather.get(
                    "rainfall",
                    0.0,
                )
                or 0.0
            ),
        }
    else:
        # 퇴근/기타 시간에는 검증되지 않은
        # 출근시간 고속도로 모델을 억지로 적용하지 않는다.
        model_weather = {
            "temperature": None,
            "humidity": None,
            "wind_speed": None,
            "rainfall": 0.0,
        }

    mapped = {
        "date": date_string,
        "start_station": data.get(
            "start_station"
        ),
        "departure_time": (
            departure_time
        ),
        "arrival_time": (
            arrival_time
        ),
        "destination": data.get(
            "destination"
        ),
        "weather": model_weather,
    }

    holiday = is_public_holiday_kr(
        date_string
    )

    token_date = _selected_date.set(
        date_string
    )

    token_holiday = (
        _selected_is_holiday.set(
            holiday
        )
    )

    try:
        with app.test_request_context(
            "/api/recommend",
            method="POST",
            json=mapped,
        ):
            original_result = (
                friend_app.recommend()
            )

        response = app.make_response(
            original_result
        )

        payload = response.get_json(
            silent=True
        )

        if not isinstance(
            payload,
            dict,
        ):
            return response

        payload[
            "weather_context"
        ] = weather

        payload[
            "model_scope"
        ] = {
            "highway_weather_model": (
                "06~08 commute only"
            ),
            "highway_weather_applied": (
                bool(
                    is_supported_highway_time
                    and payload.get(
                        "highway_prediction"
                    )
                    is not None
                )
            ),
        }

        request_info = payload.setdefault(
            "request",
            {},
        )

        request_info[
            "route_family"
        ] = route_family

        request_info[
            "risk_mode"
        ] = risk_mode

        request_info[
            "selected_boarding_stations"
        ] = data.get(
            "selected_boarding_stations",
            [],
        )

        if response.status_code < 400:
            payload = (
                apply_weather_to_candidates(
                    payload=payload,
                    date_string=date_string,
                    desired_arrival_time=(
                        arrival_time
                    ),
                    score_func=(
                        friend_app
                        .calculate_simple_score
                    ),
                )
            )

            payload = (
                select_by_route_and_risk(
                    payload=payload,
                    route_family=route_family,
                    risk_mode=risk_mode,
                )
            )

            payload["reasons"] = (
                build_reasons(
                    payload
                )
            )

        return jsonify(payload), (
            response.status_code
        )

    finally:
        _selected_date.reset(
            token_date
        )
        _selected_is_holiday.reset(
            token_holiday
        )


if __name__ == "__main__":
    app.run(
        debug=True,
        port=5001,
    )
