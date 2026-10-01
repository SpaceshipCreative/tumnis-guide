"""Fixture event streams (P1-18): loads `fixtures/streams/*.yaml` into the inputs of the
metric functions in `usage.rules`, the way the summary endpoint gathers them from the
tables. The format is described at the top of each stream file. Shared by the metric
tests (T-P1-18-03) and later evaluation numbers (P3-08)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

import yaml

from tumnis.modules.usage.rules import PlanDayFacts, PlannedOutcome

STREAMS = Path(__file__).resolve().parent / "fixtures" / "streams"
WORKING_WEEK = frozenset(range(5))
# Labels whose time is a human's: their estimate is compared with the actual (FR-4.4).
ESTIMATED_LABELS = frozenset({"human", "hybrid"})


@dataclass(frozen=True)
class Stream:
    name: str
    tz: ZoneInfo
    start: date
    end: date
    weekdays: frozenset[int]
    open_days: set[date]
    done_at: list[datetime]
    planned: list[PlannedOutcome]
    estimate_pairs: list[tuple[int, int]]
    plan_days: dict[date, PlanDayFacts]
    expect: dict[str, Any]


def _at(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def _uuid_for(key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"stream-task:{key}")


def load(path: Path) -> Stream:
    raw: dict[str, Any] = yaml.safe_load(path.read_text())
    tz = ZoneInfo(raw["timezone"])

    def local(event: dict[str, Any]) -> date:
        return _at(event["at"]).astimezone(tz).date()

    open_days: set[date] = set()
    done_at: list[datetime] = []
    pairs: list[tuple[int, int]] = []
    planned: list[str] = []
    rollovers: Counter[str] = Counter()
    published: set[date] = set()
    decided: set[date] = set()
    for event in raw.get("events") or []:
        kind = event["type"]
        if kind == "app_open":
            open_days.add(local(event))
        elif kind == "plan.published":
            published.add(event["day"])
            planned += [key for key in event.get("task_ids", []) if key not in planned]
        elif kind == "human.decided" and event.get("item_kind") == "plan_item":
            decided.add(local(event))
        elif kind == "task.status_changed" and event["to"] == "done":
            done_at.append(_at(event["at"]))
            estimate, actual = event.get("estimate"), event.get("actual")
            if event.get("label") in ESTIMATED_LABELS and estimate and actual is not None:
                pairs.append((estimate, actual))
        elif (
            kind == "task.status_changed"
            and event.get("from") == "today"
            and event["to"] == "backlog"
            and event.get("actor") == "system"
        ):
            rollovers[event["task"]] += 1
    return Stream(
        name=path.stem,
        tz=tz,
        start=raw["range"]["from"],
        end=raw["range"]["to"],
        weekdays=frozenset(raw.get("weekdays", WORKING_WEEK)),
        open_days=open_days,
        done_at=done_at,
        planned=[
            PlannedOutcome(task_id=_uuid_for(key), rollover_count=rollovers[key]) for key in planned
        ],
        estimate_pairs=pairs,
        plan_days={
            day: PlanDayFacts(published=day in published, decided=day in decided)
            for day in published | decided
        },
        expect=raw["expect"],
    )


def all_streams() -> list[Path]:
    return sorted(STREAMS.glob("*.yaml"))
