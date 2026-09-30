"""The evaluation engine the calibration page and `tumnis decisions eval` share (P3-08):
stored sets round-trip byte for byte, a bad line is named, and vLLM answers are judged
apart from Jev's at the fallback's stricter threshold."""

from __future__ import annotations

from pathlib import Path

import pytest

from tumnis.modules.decisions.eval import InvalidSet, dump_set, evaluate, load_set, set_sha256
from tumnis.modules.decisions.rules import LabeledDecision, Threshold

pytestmark = [pytest.mark.req("FR-11.5"), pytest.mark.wp("P3-08")]

SETS = Path(__file__).resolve().parents[5] / "fixtures" / "decision_eval"


def _row(provider: str, confidence: float, *, explicit: bool = True) -> LabeledDecision:
    return LabeledDecision(
        decision_point="project_match",
        model_version="vllm-local" if provider == "vllm" else "jev-1.13.0",
        provider=provider,
        answer="p01",
        confidence=confidence,
        truth="p01",
        explicit=explicit,
        input_hash="cd" * 32,
    )


def test_stored_set_round_trips() -> None:
    data = (SETS / "actionability.jsonl").read_bytes()
    rows = load_set(data)
    assert len(rows) == 120
    again = dump_set(rows)
    assert load_set(again) == rows
    assert dump_set(load_set(again)) == again
    assert set_sha256(again) == set_sha256(dump_set(rows))


def test_blank_lines_skipped_and_bad_line_named() -> None:
    line = dump_set([_row("jev", 0.9)])
    assert len(load_set(b"\n" + line + b"\n")) == 1
    with pytest.raises(InvalidSet, match="line 2"):
        load_set(line + b'{"decision_point": "project_match"}\n')


def test_fallback_answers_evaluated_apart_and_stricter() -> None:
    rows = [_row("jev", 0.9, explicit=i % 2 == 0) for i in range(120)]
    rows += [_row("vllm", 0.9) for _ in range(20)]
    found = evaluate(rows, {"project_match": Threshold(min_confidence=0.85)})
    assert [(e.provider, e.labeled, e.needed) for e in found] == [("jev", 120, 0), ("vllm", 20, 80)]
    jev, vllm = found
    assert jev.threshold == pytest.approx(0.85)
    assert jev.metrics is not None
    assert jev.metrics.auto_rate == 1
    assert jev.explicit_metrics is None  # 60 explicit labels: under 100
    assert [s.threshold for s in jev.sweep] == [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95]
    assert vllm.threshold == pytest.approx(0.95)
    assert vllm.metrics is None
    assert vllm.sweep == []


def test_point_without_threshold_uses_its_default() -> None:
    (found,) = evaluate([_row("jev", 0.5)], {})
    assert found.threshold == pytest.approx(0.85)  # project_match's plan default
