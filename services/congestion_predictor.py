import os
from datetime import datetime

import joblib
import pandas as pd


PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

MODEL_PATHS = {
    "5001A": os.path.join(
        PROJECT_ROOT,
        "models",
        "congestion_5001a_model.pkl",
    ),

    "5003A": os.path.join(
        PROJECT_ROOT,
        "models",
        "congestion_5003a_model.pkl",
    ),
}


MODELS = {
    route: joblib.load(path)
    for route, path in MODEL_PATHS.items()
}


def predict_congestion(
    route_name,
    station_seq,
    date,
    hour,
):
    """
    예상 혼잡도를 반환한다.

    route_name:
        "5001A" 또는 "5003A"

    station_seq:
        historical congestion DB의 station_seq

    date:
        "2026-09-21" 형식

    hour:
        6 ~ 10
    """

    if route_name not in MODELS:
        raise ValueError(
            f"Unsupported route: {route_name}"
        )

    dt = datetime.strptime(
        date,
        "%Y-%m-%d",
    )

    input_data = pd.DataFrame([
        {
            "station_seq": int(station_seq),
            "dow": dt.weekday(),
            "hour": int(hour),
            "month": dt.month,
        }
    ])

    prediction = float(
        MODELS[route_name]
        .predict(input_data)[0]
    )

    # 혼잡도는 음수가 될 수 없으므로 방어
    prediction = max(
        0.0,
        prediction,
    )

    return {
        "route_name": route_name,
        "station_seq": int(station_seq),
        "date": date,
        "hour": int(hour),
        "predicted_congestion": round(
            prediction,
            1,
        ),
    }
    