"""Tests for timestamp-purged forecasts and an untouched final holdout."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_DOWN, localcontext
import unittest

from hita_paper.research_forecast import (
    DEVELOPMENT,
    HOLDOUT,
    ForecastConfig,
    ForecastSample,
    evaluate_forecasts,
    forecast_rows,
)


DAY_ZERO = datetime(2026, 1, 2, 16, 30, tzinfo=UTC)


def _at(day: int) -> datetime:
    return DAY_ZERO + timedelta(days=day)


def _midnight(day: int) -> datetime:
    return _at(day).replace(hour=0, minute=0)


def _population(count: int) -> tuple[ForecastSample, ...]:
    rows = []
    for day in range(count):
        x = Decimal(day - 12) / Decimal(1000)
        rows.append(ForecastSample(
            f"slot-{day}", _at(day), (x,), x * 3 - Decimal("0.020"),
            _at(day + 1) - timedelta(hours=6), _at(day + 3) - timedelta(minutes=20),
            _at(day + 3) + timedelta(minutes=5),
        ))
    return tuple(rows)


def _config(**overrides) -> ForecastConfig:
    values = dict(
        feature_names=("momentum",), feature_scales=(Decimal("0.010"),), momentum_feature_index=0,
        holdout_start_at=_at(28), min_train_samples=5, embargo_sessions=2, ridge_alpha=Decimal("0.01"),
    )
    return ForecastConfig(**{**values, **overrides})


class ResearchForecastTests(unittest.TestCase):
    def test_training_uses_release_strictly_before_embargo_and_counts_unavailable_slots(self):
        population = list(_population(42))
        # Slot 6 closes before the boundary but its outcome is released exactly at it.
        population[6] = replace(population[6], label_available_at=_midnight(10))
        population[9] = replace(population[9], feature_values=None)
        target = forecast_rows(population, _config())[12]
        self.assertEqual(target.partition, DEVELOPMENT)
        self.assertEqual(target.training_cutoff_at, _midnight(10))
        self.assertEqual(target.training_sample_ids, tuple(f"slot-{i}" for i in range(6)))
        self.assertEqual(target.latest_training_label_available_at, _at(8) + timedelta(minutes=5))
        self.assertEqual(target.state, "FORECAST_AVAILABLE")

    def test_holdout_fit_is_fixed_and_future_label_magnitude_never_changes_forecasts(self):
        population = _population(42)
        config = _config()
        first = forecast_rows(population, config)
        modified = [replace(sample, label=Decimal("12")) if sample.decision_at >= _at(28) else sample
                    for sample in population]
        modified[-1] = replace(modified[-1], label=None, label_available_at=None)
        second = forecast_rows(reversed(modified), config)
        first_holdout = first[28:]
        self.assertTrue(all(row.partition == HOLDOUT for row in first_holdout))
        self.assertEqual(len({row.model.model_id for row in first_holdout}), 1)
        self.assertTrue(all(row.training_cutoff_at == _midnight(26) for row in first_holdout))
        self.assertTrue(all(int(sample_id.split("-")[-1]) <= 22 for sample_id in first_holdout[-1].training_sample_ids))
        self.assertEqual([row.ridge_prediction for row in first], [row.ridge_prediction for row in second])
        self.assertEqual(first_holdout[-1].model, second[-1].model)
        self.assertIsNotNone(second[-1].ridge_prediction)
        self.assertIsNone(second[-1].sample.label)

    def test_fixed_calendar_holdout_membership_does_not_shift_when_history_ends_early(self):
        config = _config()
        population = _population(42)
        shorter = forecast_rows(population[:31], config)
        self.assertEqual(shorter, forecast_rows(population, config)[:31])
        self.assertEqual(sum(row.partition == HOLDOUT for row in shorter), 3)
        self.assertEqual(sum(row.partition == HOLDOUT for row in forecast_rows(population[:26], config)), 0)

    def test_matched_metrics_and_model_coefficients_are_deterministic(self):
        population = _population(42)
        config = _config()
        rows = forecast_rows(population, config)
        report = evaluate_forecasts(population, config, rows=rows)
        with localcontext() as context:
            context.prec = 3
            context.rounding = ROUND_DOWN
            self.assertEqual(evaluate_forecasts(reversed(population), config), report)
        holdout = report["metrics"][HOLDOUT]
        self.assertEqual(holdout["matched_count"], 14)
        self.assertEqual(set(holdout["methods"]), {"ridge", "zero", "momentum"})
        methods = holdout["methods"]
        for baseline in ("zero", "momentum"):
            self.assertLess(Decimal(methods["ridge"]["mae_dec"]), Decimal(methods[baseline]["mae_dec"]))
        self.assertEqual(methods["ridge"]["directional_accuracy_dec"], "1")
        self.assertIn(report["rows"][-1]["model_id"], report["models"])

    def test_insufficient_training_and_missing_features_remain_in_coverage(self):
        population = list(_population(8))
        population[-1] = replace(population[-1], feature_values=None, label=None, label_available_at=None)
        report = evaluate_forecasts(population, _config(holdout_start_at=_at(6), min_train_samples=12))
        self.assertEqual([row["state"] for row in report["rows"][-2:]], [
            "INSUFFICIENT_TRAINING", "FEATURES_UNAVAILABLE",
        ])
        metrics = report["metrics"][HOLDOUT]
        self.assertEqual((metrics["candidate_count"], metrics["label_count"], metrics["matched_count"]), (2, 1, 0))
        self.assertIsNone(metrics["methods"]["ridge"]["mae_dec"])

    def test_duplicate_slots_and_temporally_inconsistent_labels_are_rejected(self):
        row = _population(1)[0]
        with self.assertRaisesRegex(ValueError, "unique"):
            forecast_rows((row, replace(row, sample_id="second")), _config())
        with self.assertRaisesRegex(ValueError, "released"):
            replace(row, label_available_at=row.label_start_at)
        with self.assertRaisesRegex(ValueError, "strictly after"):
            replace(row, label_start_at=row.decision_at)
        with self.assertRaisesRegex(ValueError, "feature count"):
            forecast_rows((replace(row, feature_values=()),), _config())
        with self.assertRaisesRegex(ValueError, "finite Decimals"):
            replace(row, feature_values=(0.1,))
