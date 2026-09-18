from __future__ import annotations

from datetime import datetime

from services.weather_delay_service_dhs_gpt_commited import (
    is_public_holiday_kr,
    predict_highway_weather_adjusted,
)


def predict_highway_time(
    hour,
    day_of_week=None,
    temperature=None,
    humidity=None,
    wind_speed=None,
    rainfall=0.0,
    target_date=None,
    is_public_holiday=None,
):
    """
    친구 코드의 호출 형태를 유지하는 병렬 predictor.

    검증 범위:
    - 출근시간 06/07/08시
    - 경부고속도로 분석구간 22.8 km

    temperature/humidity/wind_speed는 호출 호환을 위해 받지만
    현재 계절·강수 가중치 모델의 직접 feature로 사용하지 않는다.
    """
    if target_date is None:
        target_date = datetime.now().date()

    if is_public_holiday is None:
        is_public_holiday = (
            is_public_holiday_kr(
                target_date
            )
        )

    return predict_highway_weather_adjusted(
        target_date=target_date,
        hour=int(hour),
        rainfall_mm_per_hour=float(
            rainfall or 0.0
        ),
        is_public_holiday=is_public_holiday,
    )
