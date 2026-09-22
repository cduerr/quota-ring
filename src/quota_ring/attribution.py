"""Estimate the local model mix behind whole-percent quota observations."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from statistics import median

from quota_ring.codex_usage import TokenUsage
from quota_ring.history import Observation


@dataclass(frozen=True)
class AttributionPoint:
    observed_at: datetime
    layers: dict[str, float]

    @property
    def total(self) -> float:
        return sum(self.layers.values())


@dataclass(frozen=True)
class Attribution:
    points: tuple[AttributionPoint, ...]
    models: tuple[str, ...]
    inferred_models: tuple[str, ...]
    isolated_steps: int

    @property
    def method(self) -> str:
        if len(self.inferred_models) >= 2:
            return "observed rates"
        return "token share"


def attribute_models(
    observations: list[Observation],
    usages: list[TokenUsage],
    window_start: datetime,
) -> Attribution:
    """Allocate each observed quota step among models active since the prior step.

    Model rates are inferred only when at least two models each have enough
    isolated quota steps. Until then the allocation uses weighted token share.
    The result always preserves the reported quota curve exactly.
    """
    ordered = sorted(observations, key=lambda item: item.observed_at)
    if not ordered:
        return Attribution((), (), (), 0)

    intervals: list[tuple[Observation, dict[str, float], float]] = []
    isolated: dict[str, list[tuple[float, float]]] = defaultdict(list)
    prior_time = window_start
    prior_percent = 0.0
    for observation in ordered:
        scores = _scores_between(usages, prior_time, observation.observed_at)
        change = max(0.0, float(observation.used_percent) - prior_percent)
        intervals.append((observation, scores, change))
        if change > 0 and len(scores) == 1:
            model, score = next(iter(scores.items()))
            if score > 0:
                isolated[model].append((change, score))
        prior_time = observation.observed_at
        prior_percent = float(observation.used_percent)

    quota_samples = _quota_samples(usages)
    if sum(len(samples) for samples in quota_samples.values()) >= 4:
        isolated = quota_samples
    rates = {
        model: sum(change for change, _score in samples)
        / sum(score for _change, score in samples)
        for model, samples in isolated.items()
        if len(samples) >= 2 and sum(change for change, _score in samples) >= 2
    }
    use_inferred = len(rates) >= 2
    if not use_inferred:
        rates = {}
    default_rate = median(rates.values()) if rates else 1.0

    layers: dict[str, float] = defaultdict(float)
    points: list[AttributionPoint] = []
    models: set[str] = set()
    for observation, scores, change in intervals:
        if change > 0:
            weighted = {
                model: score * rates.get(model, default_rate) if use_inferred else score
                for model, score in scores.items()
                if score > 0
            }
            denominator = sum(weighted.values())
            if denominator:
                for model, score in weighted.items():
                    layers[model] += change * score / denominator
                    models.add(model)
            else:
                layers["unattributed"] += change
                models.add("unattributed")
        points.append(AttributionPoint(observation.observed_at, dict(layers)))

    return Attribution(
        tuple(points),
        tuple(sorted(models, key=lambda model: (-layers[model], model))),
        tuple(sorted(rates)) if use_inferred else (),
        sum(len(samples) for samples in isolated.values()),
    )


def _scores_between(
    usages: list[TokenUsage], start: datetime, end: datetime
) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for usage in usages:
        if start < usage.observed_at <= end and usage.weighted_tokens > 0:
            scores[usage.model] += usage.weighted_tokens
    return dict(scores)


def _quota_samples(
    usages: list[TokenUsage],
) -> dict[str, list[tuple[float, float]]]:
    """Find model-only stretches between quota changes reported by Codex."""
    samples: dict[str, list[tuple[float, float]]] = defaultdict(list)
    pending: dict[str, float] = defaultdict(float)
    prior_percent = None
    for usage in sorted(usages, key=lambda item: item.observed_at):
        if usage.quota_percent is None:
            continue
        if prior_percent is None:
            prior_percent = usage.quota_percent
            pending.clear()
            continue
        pending[usage.model] += usage.weighted_tokens
        if usage.quota_percent <= prior_percent:
            continue
        change = usage.quota_percent - prior_percent
        active = {model: score for model, score in pending.items() if score > 0}
        # A multi-point jump first seen by a local request usually accumulated
        # while no local session was running. Treating all of it as the cost of
        # that request badly inflates its model's inferred rate.
        if change <= 1.01 and len(active) == 1:
            model, score = next(iter(active.items()))
            samples[model].append((change, score))
        pending.clear()
        prior_percent = usage.quota_percent
    return samples
