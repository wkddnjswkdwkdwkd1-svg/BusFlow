from services.recommendation_postprocess_dhs_gpt_commited import (
    apply_weather_to_candidates,
    select_by_route_and_risk,
)
from services.weather_delay_service_dhs_gpt_commited import (
    get_weather_delay_summary,
    predict_highway_weather_adjusted,
)


def _score(seats, arrival_seconds, deadline_met):
    score = 0

    if seats is not None:
        score += min(max(seats, 0), 30)

    if deadline_met is False:
        score -= 100

    return score


def test_rain_intensity_is_monotonic_in_summer():
    values = [
        get_weather_delay_summary(
            "2026-07-15",
            rain,
        )["weather_delay_percent"]
        for rain in [
            0,
            1,
            3,
            10,
            20,
        ]
    ]

    assert values == sorted(values)


def test_summer_weekday_7am_rain_increases_time():
    dry = predict_highway_weather_adjusted(
        "2026-07-15",
        7,
        0.0,
    )

    rainy = predict_highway_weather_adjusted(
        "2026-07-15",
        7,
        4.2,
    )

    assert (
        rainy["highway_minutes"]
        > dry["highway_minutes"]
    )

    assert (
        rainy["weather_delay_percent"]
        > 0
    )


def test_weather_delta_is_added_to_full_route_time():
    payload = {
        "highway_prediction": {
            "baseline_highway_minutes": 20.0,
            "highway_minutes": 23.0,
        },
        "alternatives": [
            {
                "route": "5001A",
                "travel_time_minutes": 50.0,
                "departure_time": "07:10",
                "remain_seats": 15,
                "arrival_seconds": 300,
                "strategy_adjustment": 0,
                "boarding_strategy": "take_first_bus",
            }
        ],
    }

    result = apply_weather_to_candidates(
        payload,
        "2026-07-15",
        "08:30",
        _score,
    )

    candidate = result["alternatives"][0]

    assert candidate[
        "historical_travel_time_minutes"
    ] == 50.0

    assert candidate[
        "weather_highway_extra_minutes"
    ] == 3.0

    assert candidate[
        "travel_time_minutes"
    ] == 53.0


def test_route_family_filters_other_route():
    payload = {
        "alternatives": [
            {
                "route": "5001A",
                "final_score": 20,
                "deadline_met": True,
                "boarding_strategy": "take_first_bus",
                "arrival_seconds": 600,
                "remain_seats": 10,
            },
            {
                "route": "5003A",
                "final_score": 99,
                "deadline_met": True,
                "boarding_strategy": "take_first_bus",
                "arrival_seconds": 200,
                "remain_seats": 20,
            },
        ],
        "boarding_strategies": [],
    }

    result = select_by_route_and_risk(
        payload,
        route_family="5001",
        risk_mode="safe",
    )

    assert result["recommended"]["route"] == "5001A"
    assert all(
        item["route"].startswith("5001")
        for item in result["alternatives"]
    )


def test_fast_mode_prefers_earlier_bus():
    payload = {
        "alternatives": [
            {
                "route": "5001A",
                "final_score": 80,
                "deadline_met": True,
                "boarding_strategy": "take_first_bus",
                "arrival_seconds": 600,
                "remain_seats": 20,
            },
            {
                "route": "5001A",
                "final_score": 20,
                "deadline_met": True,
                "boarding_strategy": "take_first_bus",
                "arrival_seconds": 120,
                "remain_seats": 5,
            },
        ],
        "boarding_strategies": [],
    }

    result = select_by_route_and_risk(
        payload,
        route_family="5001",
        risk_mode="fast",
    )

    assert (
        result["recommended"]["arrival_seconds"]
        == 120
    )
