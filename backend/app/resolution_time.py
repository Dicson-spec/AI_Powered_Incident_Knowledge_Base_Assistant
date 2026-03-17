import csv
import math
from dataclasses import dataclass
from datetime import datetime

from sklearn.ensemble import RandomForestRegressor
from sklearn.feature_extraction import DictVectorizer
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import train_test_split

from .config import settings


DATETIME_FORMATS = (
    "%m/%d/%Y %H:%M",
    "%d/%m/%Y %H:%M",
    "%m-%d-%Y %H:%M",
    "%d-%m-%Y %H:%M",
)
CREATED_COLUMNS = ("created_time", "Created_Time", "open_time", "Open_Time")
RESOLVED_COLUMNS = ("resolved_time", "Resolved_Time")


@dataclass(slots=True)
class ResolutionTimeMetrics:
    mae_minutes: float
    rmse_minutes: float
    train_samples: int
    test_samples: int


class ResolutionTimePredictor:
    def __init__(self) -> None:
        self._vectorizer = DictVectorizer(sparse=True)
        self._model = RandomForestRegressor(
            n_estimators=200,
            random_state=42,
            n_jobs=-1,
        )
        self._metrics: ResolutionTimeMetrics | None = None
        self._ready = False

    @property
    def metrics(self) -> ResolutionTimeMetrics | None:
        return self._metrics

    def train(self) -> None:
        rows = self._load_rows()
        if len(rows) < 50:
            raise RuntimeError("Not enough ITSM rows with valid timestamps to train resolution time model.")

        features = [row["features"] for row in rows]
        targets = [row["resolution_time_minutes"] for row in rows]

        X = self._vectorizer.fit_transform(features)
        X_train, X_test, y_train, y_test = train_test_split(
            X,
            targets,
            test_size=0.2,
            random_state=42,
        )

        self._model.fit(X_train, y_train)
        predictions = self._model.predict(X_test)
        mae = mean_absolute_error(y_test, predictions)
        rmse = math.sqrt(mean_squared_error(y_test, predictions))
        self._metrics = ResolutionTimeMetrics(
            mae_minutes=mae,
            rmse_minutes=rmse,
            train_samples=len(y_train),
            test_samples=len(y_test),
        )
        self._ready = True

    def predict(self, category: str, ci_category: str, ci_subcategory: str) -> float:
        if not self._ready:
            raise RuntimeError("Resolution time predictor is not trained.")

        features = {
            "category": (category or "").strip(),
            "ci_category": (ci_category or "").strip(),
            "ci_subcategory": (ci_subcategory or "").strip(),
        }
        encoded = self._vectorizer.transform([features])
        prediction = float(self._model.predict(encoded)[0])
        return prediction

    def _load_rows(self) -> list[dict]:
        rows: list[dict] = []
        with settings.itsm_dataset_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                created_time = self._pick_timestamp(row, CREATED_COLUMNS)
                resolved_time = self._pick_timestamp(row, RESOLVED_COLUMNS)
                if not created_time or not resolved_time or resolved_time < created_time:
                    continue

                resolution_time_minutes = (resolved_time - created_time).total_seconds() / 60
                rows.append(
                    {
                        "resolution_time_minutes": resolution_time_minutes,
                        "features": {
                            "category": (row.get("Category") or "").strip(),
                            "ci_category": (row.get("CI_Cat") or "").strip(),
                            "ci_subcategory": (row.get("CI_Subcat") or "").strip(),
                        },
                    }
                )
        return rows

    def _pick_timestamp(self, row: dict, candidates: tuple[str, ...]) -> datetime | None:
        for column in candidates:
            if column in row:
                timestamp = self._parse_datetime(row.get(column, ""))
                if timestamp:
                    return timestamp
        return None

    def _parse_datetime(self, value: str) -> datetime | None:
        cleaned = (value or "").strip()
        if not cleaned:
            return None
        for fmt in DATETIME_FORMATS:
            try:
                return datetime.strptime(cleaned, fmt)
            except ValueError:
                continue
        return None
