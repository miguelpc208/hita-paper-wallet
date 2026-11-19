"""Chronological, research-only ridge forecasts and matched predictive metrics."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from typing import Iterable

from .contracts import content_digest, time_text, utc

MODEL_VERSION = "hita.research.ridge.v1"
ZERO = Decimal(0)
CALCULATION = Context(prec=50, rounding=ROUND_HALF_EVEN)
DEVELOPMENT = "DEVELOPMENT"
HOLDOUT = "HOLDOUT"


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else "0" if value.is_zero() else format(value, "f")


def _time_text(value: datetime | None) -> str | None:
    return None if value is None else time_text(value)


def _finite_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite()


@dataclass(frozen=True, slots=True)
class ForecastSample:
    sample_id: str
    decision_at: datetime
    feature_values: tuple[Decimal, ...] | None
    label: Decimal | None
    label_start_at: datetime | None
    label_end_at: datetime | None
    label_available_at: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.sample_id, str) or not self.sample_id.strip():
            raise ValueError("A forecast sample needs an ID")
        object.__setattr__(self, "decision_at", utc(self.decision_at))
        for field in ("label_start_at", "label_end_at", "label_available_at"):
            timestamp = getattr(self, field)
            if timestamp is not None:
                object.__setattr__(self, field, utc(timestamp))
        if self.feature_values is not None and (
            not isinstance(self.feature_values, tuple) or not all(map(_finite_decimal, self.feature_values))
        ):
            raise ValueError("Forecast features must be finite Decimals")
        if self.label is not None and (
            not _finite_decimal(self.label)
            or self.label_start_at is None
            or self.label_end_at is None
            or self.label_available_at is None
        ):
            raise ValueError("A numeric label needs a finite Decimal and complete outcome clocks")
        if self.label_start_at is not None and self.label_start_at <= self.decision_at:
            raise ValueError("A label's exposure must start strictly after its decision")
        if self.label_end_at is not None and (
            self.label_start_at is None or self.label_end_at < self.label_start_at
        ):
            raise ValueError("A label's exposure end must follow its start")
        if self.label_available_at is not None and (
            self.label_end_at is None or self.label_available_at < self.label_end_at
        ):
            raise ValueError("A label cannot be released before its exposure ends")


@dataclass(frozen=True, slots=True)
class ForecastConfig:
    feature_names: tuple[str, ...]
    feature_scales: tuple[Decimal, ...]
    momentum_feature_index: int
    holdout_start_at: datetime
    min_train_samples: int = 12
    embargo_sessions: int = 2
    ridge_alpha: Decimal = Decimal(1)

    def __post_init__(self) -> None:
        object.__setattr__(self, "holdout_start_at", utc(self.holdout_start_at))
        if (
            not isinstance(self.feature_names, tuple)
            or not self.feature_names
            or any(not isinstance(name, str) or not name.strip() for name in self.feature_names)
            or len(set(self.feature_names)) != len(self.feature_names)
            or not isinstance(self.feature_scales, tuple)
            or len(self.feature_names) != len(self.feature_scales)
            or any(not _finite_decimal(scale) or scale <= ZERO for scale in self.feature_scales)
        ):
            raise ValueError("Distinct feature names require one positive Decimal scale each")
        if type(self.momentum_feature_index) is not int or not 0 <= self.momentum_feature_index < len(self.feature_names):
            raise ValueError("Momentum index must select a configured feature")
        if type(self.min_train_samples) is not int or self.min_train_samples < 2:
            raise ValueError("Ridge requires at least two training samples")
        if type(self.embargo_sessions) is not int or self.embargo_sessions < 0:
            raise ValueError("Embargo sessions must be a nonnegative integer")
        if not _finite_decimal(self.ridge_alpha) or self.ridge_alpha <= ZERO:
            raise ValueError("Ridge regularization must be a positive Decimal")

    def to_dict(self) -> dict:
        return {
            "model_version": MODEL_VERSION,
            "feature_names": list(self.feature_names),
            "feature_scales_dec": [_decimal_text(scale) for scale in self.feature_scales],
            "momentum_feature_index": self.momentum_feature_index,
            "holdout_start_at": time_text(self.holdout_start_at),
            "min_train_samples": self.min_train_samples,
            "embargo_sessions": self.embargo_sessions,
            "embargo_clock": "UTC_MIDNIGHT_OF_FIRST_EMBARGOED_SESSION",
            "ridge_alpha_dec": _decimal_text(self.ridge_alpha),
            "scale_fit": "FIXED_BEFORE_EVALUATION",
        }


@dataclass(frozen=True, slots=True)
class RidgeModel:
    model_id: str
    intercept: Decimal
    coefficients: tuple[Decimal, ...]

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "intercept_dec": _decimal_text(self.intercept),
            "scaled_feature_coefficients_dec": [_decimal_text(value) for value in self.coefficients],
        }


@dataclass(frozen=True, slots=True)
class ForecastRow:
    sample: ForecastSample
    partition: str
    state: str
    model: RidgeModel | None
    training_cutoff_at: datetime | None
    training_sample_ids: tuple[str, ...]
    latest_training_label_available_at: datetime | None
    ridge_prediction: Decimal | None
    momentum_prediction: Decimal | None
    zero_prediction: Decimal = ZERO

    def to_dict(self) -> dict:
        return {
            "sample_id": self.sample.sample_id,
            "partition": self.partition,
            "state": self.state,
            "decision_at": time_text(self.sample.decision_at),
            "feature_values_dec": (
                [_decimal_text(value) for value in self.sample.feature_values]
                if self.sample.feature_values is not None else None
            ),
            "label_dec": _decimal_text(self.sample.label),
            "label_start_at": _time_text(self.sample.label_start_at),
            "label_end_at": _time_text(self.sample.label_end_at),
            "label_available_at": _time_text(self.sample.label_available_at),
            "model_id": self.model.model_id if self.model is not None else None,
            "training_cutoff_at": _time_text(self.training_cutoff_at),
            "training_sample_ids": list(self.training_sample_ids),
            "latest_training_label_available_at": _time_text(self.latest_training_label_available_at),
            "ridge_prediction_dec": _decimal_text(self.ridge_prediction),
            "momentum_prediction_dec": _decimal_text(self.momentum_prediction),
            "zero_prediction_dec": _decimal_text(self.zero_prediction),
        }


def _scaled(values: tuple[Decimal, ...], config: ForecastConfig) -> tuple[Decimal, ...]:
    return tuple(value / scale for value, scale in zip(values, config.feature_scales, strict=True))


def _solve(matrix: list[list[Decimal]], target: list[Decimal]) -> tuple[Decimal, ...]:
    """Cholesky solve for the positive-definite regularized normal equations."""
    degrees = len(target)
    lower = [[ZERO] * degrees for _ in range(degrees)]
    for row in range(degrees):
        for column in range(row + 1):
            difference = matrix[row][column] - sum(
                (lower[row][i] * lower[column][i] for i in range(column)), ZERO,
            )
            lower[row][column] = difference.sqrt() if row == column else difference / lower[column][column]
    intermediate: list[Decimal] = []
    for row in range(degrees):
        residual = target[row] - sum((lower[row][i] * intermediate[i] for i in range(row)), ZERO)
        intermediate.append(residual / lower[row][row])
    coefficients = [ZERO] * degrees
    for row in range(degrees - 1, -1, -1):
        residual = intermediate[row] - sum(
            (lower[i][row] * coefficients[i] for i in range(row + 1, degrees)), ZERO,
        )
        coefficients[row] = residual / lower[row][row]
    return tuple(coefficients)


def _fit(samples: tuple[ForecastSample, ...], config: ForecastConfig) -> RidgeModel:
    features = [_scaled(sample.feature_values, config) for sample in samples]
    labels = [sample.label for sample in samples]
    rows = len(samples)
    degrees = len(config.feature_names)
    means = tuple(sum(column, ZERO) / rows for column in zip(*features, strict=True))
    label_mean = sum(labels, ZERO) / rows
    centered_columns = tuple(
        tuple(row[column] - means[column] for row in features) for column in range(degrees)
    )
    centered_labels = tuple(label - label_mean for label in labels)
    matrix = [
        [
            sum((left * right for left, right in zip(column, other, strict=True)), ZERO)
            + (config.ridge_alpha if i == j else ZERO)
            for j, other in enumerate(centered_columns)
        ]
        for i, column in enumerate(centered_columns)
    ]
    target = [
        sum((feature * label for feature, label in zip(column, centered_labels, strict=True)), ZERO)
        for column in centered_columns
    ]
    coefficients = _solve(matrix, target)
    intercept = label_mean - sum((mean * coef for mean, coef in zip(means, coefficients, strict=True)), ZERO)
    identity = content_digest({
        "config": config.to_dict(),
        "training": [
            {
                "sample_id": sample.sample_id,
                "decision_at": time_text(sample.decision_at),
                "label_start_at": time_text(sample.label_start_at),
                "label_end_at": time_text(sample.label_end_at),
                "label_available_at": time_text(sample.label_available_at),
                "features_dec": [_decimal_text(value) for value in sample.feature_values],
                "label_dec": _decimal_text(sample.label),
            }
            for sample in samples
        ],
        "intercept_dec": _decimal_text(intercept),
        "coefficients_dec": [_decimal_text(value) for value in coefficients],
    })
    return RidgeModel(f"ridge-{identity}", intercept, coefficients)


@dataclass(frozen=True, slots=True)
class _Training:
    cutoff: datetime | None
    sample_ids: tuple[str, ...]
    latest_label_available_at: datetime | None
    model: RidgeModel | None


def _training_cutoff(boundary_clock: datetime, fit_clock: datetime, config: ForecastConfig) -> datetime:
    if config.embargo_sessions:
        boundary_clock = boundary_clock.replace(hour=0, minute=0, second=0, microsecond=0)
    return min(boundary_clock, fit_clock)


def holdout_training_cutoff(decision_slots: Iterable[datetime], config: ForecastConfig) -> datetime | None:
    """Find the outer cutoff from the calendar, even before holdout data is observed."""
    times = tuple(sorted(utc(instant) for instant in decision_slots))
    start = bisect_left(times, config.holdout_start_at)
    boundary = start - config.embargo_sessions
    if start == len(times) or boundary < 0:
        return None
    return _training_cutoff(times[boundary], config.holdout_start_at, config)


def _train_before(
    ordered: tuple[ForecastSample, ...],
    index: int,
    fit_clock: datetime,
    config: ForecastConfig,
) -> _Training:
    boundary = index - config.embargo_sessions
    if boundary < 0:
        return _Training(None, (), None, None)
    cutoff = _training_cutoff(ordered[boundary].decision_at, fit_clock, config)
    training = tuple(
        sample for sample in ordered[:boundary]
        if sample.feature_values is not None and sample.label is not None and sample.label_available_at < cutoff
    )
    model = _fit(training, config) if len(training) >= config.min_train_samples else None
    return _Training(
        cutoff,
        tuple(sample.sample_id for sample in training),
        max((sample.label_available_at for sample in training), default=None),
        model,
    )


def forecast_rows(samples: Iterable[ForecastSample], config: ForecastConfig) -> tuple[ForecastRow, ...]:
    """Forecast every decision slot and reuse one frozen fit throughout the holdout.

    Include all session decision slots, including unavailable features and outcomes,
    because embargo lengths count full UTC session dates immediately before the
    forecast. An outcome whose release equals the training cutoff remains excluded.
    """
    ordered = tuple(sorted(samples, key=lambda sample: sample.decision_at))
    times = tuple(sample.decision_at for sample in ordered)
    if len(set(times)) != len(times) or len({sample.sample_id for sample in ordered}) != len(ordered):
        raise ValueError("Forecast samples must have unique IDs and decision slots")
    if any(
        sample.feature_values is not None and len(sample.feature_values) != len(config.feature_names)
        for sample in ordered
    ):
        raise ValueError("Forecast feature count differs from the fixed feature specification")
    boundary = bisect_left(times, config.holdout_start_at)
    rows = []
    with localcontext(CALCULATION):
        holdout = _train_before(ordered, boundary, config.holdout_start_at, config) if boundary < len(ordered) else None
        for index, sample in enumerate(ordered):
            if index < boundary:
                partition = DEVELOPMENT
                training = _train_before(ordered, index, sample.decision_at, config)
            else:
                partition, training = HOLDOUT, holdout
            ridge = None
            momentum = None
            if sample.feature_values is None:
                state = "FEATURES_UNAVAILABLE"
            else:
                momentum = sample.feature_values[config.momentum_feature_index]
                state = "INSUFFICIENT_TRAINING"
                if training.model is not None:
                    features = _scaled(sample.feature_values, config)
                    ridge = training.model.intercept + sum(
                        (weight * value for weight, value in zip(training.model.coefficients, features, strict=True)), ZERO,
                    )
                    state = "FORECAST_AVAILABLE"
            rows.append(ForecastRow(
                sample=sample,
                partition=partition,
                state=state,
                model=training.model,
                training_cutoff_at=training.cutoff,
                training_sample_ids=training.sample_ids,
                latest_training_label_available_at=training.latest_label_available_at,
                ridge_prediction=ridge,
                momentum_prediction=momentum,
            ))
    return tuple(rows)


def _predictive_metrics(predictions: tuple[Decimal, ...], labels: tuple[Decimal, ...]) -> dict:
    count = len(labels)
    if count == 0:
        return dict.fromkeys((
            "mae_dec", "rmse_dec", "directional_accuracy_dec", "mean_error_dec",
            "mean_prediction_dec", "mean_observed_dec",
        ))
    errors = tuple(predicted - observed for predicted, observed in zip(predictions, labels, strict=True))
    sign_hits = sum(
        (predicted > ZERO, predicted < ZERO) == (observed > ZERO, observed < ZERO)
        for predicted, observed in zip(predictions, labels, strict=True)
    )
    return {
        "mae_dec": _decimal_text(sum(map(abs, errors), ZERO) / count),
        "rmse_dec": _decimal_text((sum((error * error for error in errors), ZERO) / count).sqrt()),
        "directional_accuracy_dec": _decimal_text(Decimal(sign_hits) / count),
        "mean_error_dec": _decimal_text(sum(errors, ZERO) / count),
        "mean_prediction_dec": _decimal_text(sum(predictions, ZERO) / count),
        "mean_observed_dec": _decimal_text(sum(labels, ZERO) / count),
    }


def _partition_metrics(rows: tuple[ForecastRow, ...], *, label_cutoff: datetime | None = None) -> dict:
    labeled = tuple(row for row in rows if row.sample.label is not None)
    scoreable = tuple(
        row for row in labeled if label_cutoff is None or row.sample.label_available_at < label_cutoff
    )
    matched = tuple(row for row in scoreable if row.ridge_prediction is not None)
    labels = tuple(row.sample.label for row in matched)
    return {
        "candidate_count": len(rows),
        "feature_count": sum(row.sample.feature_values is not None for row in rows),
        "label_count": len(labeled),
        "scoring_label_cutoff_at": _time_text(label_cutoff),
        "labels_excluded_by_scoring_cutoff": len(labeled) - len(scoreable),
        "prediction_count": sum(row.ridge_prediction is not None for row in rows),
        "matched_count": len(matched),
        "methods": {
            method: _predictive_metrics(tuple(getattr(row, f"{method}_prediction") for row in matched), labels)
            for method in ("ridge", "zero", "momentum")
        },
    }


def summarize_forecasts(
    rows: Iterable[ForecastRow], config: ForecastConfig, *, development_label_cutoff: datetime | None = None,
) -> dict:
    """Return an auditable report for rows produced by `forecast_rows`."""
    population = tuple(rows)
    models = {}
    for row in population:
        if row.model is not None:
            models.setdefault(row.model.model_id, row.model.to_dict())
    with localcontext(CALCULATION):
        metrics = {
            partition: _partition_metrics(
                tuple(row for row in population if row.partition == partition),
                label_cutoff=development_label_cutoff if partition == DEVELOPMENT else None,
            )
            for partition in (DEVELOPMENT, HOLDOUT)
        }
    return {
        "config": config.to_dict(),
        "rows": [row.to_dict() for row in population],
        "models": models,
        "metrics": metrics,
    }


def evaluate_forecasts(
    samples: Iterable[ForecastSample],
    config: ForecastConfig,
    *,
    rows: Iterable[ForecastRow] | None = None,
    development_label_cutoff: datetime | None = None,
) -> dict:
    if rows is None:
        population = forecast_rows(samples, config)
    else:
        population = tuple(rows)
        ordered_samples = tuple(sorted(samples, key=lambda sample: sample.decision_at))
        if tuple(row.sample for row in population) != ordered_samples:
            raise ValueError("Precomputed forecasts must cover the given chronological sample population")
    return summarize_forecasts(population, config, development_label_cutoff=development_label_cutoff)
