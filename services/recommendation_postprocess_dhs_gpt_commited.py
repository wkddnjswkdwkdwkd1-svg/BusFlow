from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable


def _route_matches(
    route_name: str | None,
    route_family: str | None,
) -> bool:
    if not route_family:
        return True

    return str(route_name or "").startswith(
        str(route_family)
    )


def _deadline_priority(value) -> int:
    if value is True:
        return 2
    if value is None:
        return 1
    return 0


def apply_weather_to_candidates(
    payload: dict,
    date_string: str,
    desired_arrival_time: str,
    score_func: Callable,
) -> dict:
    """
    친구의 full-route p75 시간에
    '비 때문에 늘어난 고속도로 시간'만 추가한다.

    full-route baseline 자체를 새 고속도로 모델로 대체하지 않기 때문에
    로컬 도로/정류장 이동시간을 중복 계산하지 않는다.
    """
    highway = payload.get(
        "highway_prediction"
    ) or {}

    adjusted = highway.get(
        "highway_minutes"
    )
    baseline = highway.get(
        "baseline_highway_minutes"
    )

    if (
        adjusted is None
        or baseline is None
    ):
        payload[
            "weather_route_adjustment_applied"
        ] = False
        return payload

    delta = max(
        0.0,
        float(adjusted)
        - float(baseline),
    )

    try:
        desired_arrival = datetime.strptime(
            f"{date_string} "
            f"{desired_arrival_time}",
            "%Y-%m-%d %H:%M",
        )
    except ValueError:
        desired_arrival = None

    for candidate in payload.get(
        "alternatives",
        [],
    ):
        original_minutes = candidate.get(
            "travel_time_minutes"
        )

        if original_minutes is None:
            continue

        original_minutes = float(
            original_minutes
        )

        final_minutes = (
            original_minutes + delta
        )

        candidate[
            "historical_travel_time_minutes"
        ] = round(
            original_minutes,
            1,
        )

        candidate[
            "weather_highway_extra_minutes"
        ] = round(
            delta,
            1,
        )

        candidate[
            "travel_time_minutes"
        ] = round(
            final_minutes,
            1,
        )

        departure_text = candidate.get(
            "departure_time"
        )

        estimated_arrival = None

        if departure_text:
            try:
                departure = datetime.strptime(
                    f"{date_string} "
                    f"{departure_text}",
                    "%Y-%m-%d %H:%M",
                )

                estimated_arrival = (
                    departure
                    + timedelta(
                        minutes=final_minutes
                    )
                )
            except ValueError:
                estimated_arrival = None

        if estimated_arrival is not None:
            candidate[
                "estimated_arrival_time"
            ] = estimated_arrival.strftime(
                "%H:%M"
            )

            if desired_arrival is not None:
                candidate["deadline_met"] = (
                    estimated_arrival
                    <= desired_arrival
                )

        base_score = score_func(
            candidate.get("remain_seats"),
            candidate.get("arrival_seconds"),
            candidate.get("deadline_met"),
        )

        candidate["base_score"] = (
            base_score
        )

        candidate["final_score"] = round(
            float(base_score)
            + float(
                candidate.get(
                    "strategy_adjustment",
                    0,
                )
                or 0
            ),
            1,
        )

    payload[
        "weather_route_adjustment_applied"
    ] = delta > 0

    payload[
        "weather_highway_extra_minutes"
    ] = round(delta, 1)

    return payload


def select_by_route_and_risk(
    payload: dict,
    route_family: str | None,
    risk_mode: str = "safe",
) -> dict:
    alternatives = [
        item
        for item in payload.get(
            "alternatives",
            [],
        )
        if _route_matches(
            item.get("route"),
            route_family,
        )
    ]

    strategies = [
        item
        for item in payload.get(
            "boarding_strategies",
            [],
        )
        if _route_matches(
            item.get("route"),
            route_family,
        )
    ]

    eligible = [
        item
        for item in alternatives
        if (
            float(
                item.get(
                    "final_score",
                    -9999,
                )
                or -9999
            )
            >= 0
            and item.get(
                "boarding_strategy"
            )
            not in {
                "move_upstream",
                "high_risk",
            }
            and item.get(
                "deadline_met"
            )
            is not False
        )
    ]

    if risk_mode == "fast":
        eligible.sort(
            key=lambda item: (
                _deadline_priority(
                    item.get(
                        "deadline_met"
                    )
                ),
                -float(
                    item.get(
                        "arrival_seconds",
                        10**9,
                    )
                    or 10**9
                ),
                float(
                    item.get(
                        "final_score",
                        -9999,
                    )
                    or -9999
                ),
            ),
            reverse=True,
        )
    else:
        eligible.sort(
            key=lambda item: (
                _deadline_priority(
                    item.get(
                        "deadline_met"
                    )
                ),
                float(
                    item.get(
                        "final_score",
                        -9999,
                    )
                    or -9999
                ),
                float(
                    item.get(
                        "remain_seats",
                        -1,
                    )
                    if item.get(
                        "remain_seats"
                    )
                    is not None
                    else -1
                ),
            ),
            reverse=True,
        )

    recommended = (
        eligible[0]
        if eligible
        else None
    )

    recommended_strategy = None

    if recommended is None:
        move_items = [
            item
            for item in strategies
            if item.get("strategy")
            == "move_upstream"
        ]

        if move_items:
            move_items.sort(
                key=lambda item: float(
                    item.get(
                        "alternative_score",
                        0,
                    )
                    or 0
                ),
                reverse=True,
            )
            recommended_strategy = (
                move_items[0]
            )
        else:
            risk_items = [
                item
                for item in strategies
                if item.get("strategy")
                == "high_risk"
            ]

            if risk_items:
                recommended_strategy = (
                    risk_items[0]
                )

    payload["alternatives"] = alternatives
    payload[
        "boarding_strategies"
    ] = strategies
    payload["recommended"] = recommended
    payload[
        "recommended_strategy"
    ] = recommended_strategy

    return payload


def build_reasons(
    payload: dict,
) -> list[str]:
    reasons = []
    recommended = payload.get(
        "recommended"
    )

    if recommended is not None:
        seats = recommended.get(
            "remain_seats"
        )

        if seats is not None:
            if seats >= 20:
                reasons.append(
                    "현재 잔여좌석이 "
                    "비교적 여유롭습니다."
                )
            elif seats >= 10:
                reasons.append(
                    "현재 좌석 여유가 있습니다."
                )
            elif seats >= 5:
                reasons.append(
                    "탑승 가능한 좌석은 남아 있지만 "
                    "여유가 크지 않습니다."
                )
            else:
                reasons.append(
                    "현재 잔여좌석이 매우 적습니다."
                )

        historical = recommended.get(
            "historical_congestion"
        )

        if historical:
            reasons.append(
                "과거 동일 요일·시간대 "
                f"평균 혼잡도는 "
                f"{historical.get('avg_congestion')}입니다."
            )

        if recommended.get(
            "estimated_arrival_time"
        ):
            reasons.append(
                "예상 도착 시각은 "
                f"{recommended['estimated_arrival_time']}입니다."
            )

    elif payload.get(
        "recommended_strategy"
    ):
        strategy = payload[
            "recommended_strategy"
        ]
        reasons.append(
            strategy.get(
                "message",
                "앞 정류장 선탑승 전략을 "
                "확인해보세요.",
            )
        )

    highway = payload.get(
        "highway_prediction"
    ) or {}

    if highway.get(
        "highway_minutes"
    ) is not None:
        reasons.append(
            "기상 예보를 반영한 "
            "고속도로 구간 예상 주행시간은 약 "
            f"{highway['highway_minutes']}분입니다."
        )

    if payload.get(
        "weather_route_adjustment_applied"
    ):
        reasons.append(
            "예보 강수량을 최종 이동시간에 "
            "반영했습니다."
        )

    return reasons
