"""Digest rules (P2-03, FR-13.1): where a read starts from its cursor (`resolve_start`), and
which digest entries each event makes (`classify_event`)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

TASK = uuid.UUID("0192a000-0000-7000-8000-000000000001")
PROJECT = uuid.UUID("0192a000-0000-7000-8000-000000000002")
OTHER_TASK = uuid.UUID("0192a000-0000-7000-8000-000000000003")
ITEM = uuid.UUID("0192a000-0000-7000-8000-000000000004")
DOCUMENT = uuid.UUID("0192a000-0000-7000-8000-000000000005")


@pytest.mark.req("FR-13.1")
@pytest.mark.wp("P2-03")
def test_resolve_start_table() -> None:
    """T-P2-03-02
    Every branch of `resolve_start(acked, since, issued_max) -> (new_acked, read_from)`:
    no cursor reads from the acknowledged position (the last digest may come again); a
    cursor past everything issued is refused; a cursor at or past the acknowledged one
    acknowledges it and reads from it; an older cursor reads from the acknowledged
    position, so acknowledged entries never come again.
    """
    from tumnis.modules.agents.rules import (  # noqa: PLC0415
        DigestCursorInvalid,
        Pos,
        resolve_start,
    )

    acked, issued = Pos(10, 2), Pos(40, 1)
    table: list[tuple[Pos | None, tuple[Pos, Pos]]] = [
        (None, (acked, acked)),
        (Pos(10, 2), (acked, acked)),  # since == acked
        (Pos(25, 7), (Pos(25, 7), Pos(25, 7))),  # between acked and issued: ack it
        (Pos(40, 1), (Pos(40, 1), Pos(40, 1))),  # since == issued
        (Pos(10, 1), (acked, acked)),  # stale: same tx, lower seq
        (Pos(3, 9), (acked, acked)),  # stale: lower tx
    ]
    for since, expected in table:
        assert resolve_start(acked, since, issued) == expected, since
    for forged in (Pos(40, 2), Pos(41, 0), Pos(10**15, 1)):
        with pytest.raises(DigestCursorInvalid):
            resolve_start(acked, forged, issued)
    # A consumer that never read has nothing acknowledged or issued yet.
    zero = Pos(0, 0)
    assert resolve_start(zero, None, zero) == (zero, zero)
    with pytest.raises(DigestCursorInvalid):
        resolve_start(zero, Pos(0, 1), zero)
    assert Pos(1, 0) > Pos(0, 99)  # ordered by tx, then seq


def _task(actual: int | None = None, estimate: int | None = 60) -> Any:
    from tumnis.modules.agents.rules import TaskFacts  # noqa: PLC0415

    return TaskFacts(
        task_id=TASK, project_id=PROJECT, estimate_minutes=estimate, actual_minutes=actual
    )


def _decided(item_kind: str, decision: str, **extra: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "item_kind": item_kind,
        "item_id": str(ITEM),
        "target_type": "task",
        "target_id": str(TASK),
        "decision": decision,
        **extra,
    }


def _cases() -> list[tuple[str, str, dict[str, Any], Any, set[tuple[str, str]]]]:
    """(case, event name, payload, task facts, expected {(kind, scope)})."""
    task = _task()
    doc = {
        "schema_version": 1,
        "document_id": str(DOCUMENT),
        "version_id": str(uuid.uuid4()),
        "version_no": 2,
        "title": "Standing rules",
        "trust": "trusted",
        "size": 120,
    }
    return [
        (
            "label override (P1-07)",
            "human.decided",
            _decided(
                "label_override",
                "ai",
                previous={"label": "human", "label_source": "jev"},
                payload={"value": "ai", "overridden": True},
            ),
            task,
            {("label_override", "project")},
        ),
        (
            "label item edited in review",
            "human.decided",
            _decided("label", "edit", previous={"label": "human"}, payload={"value": "hybrid"}),
            task,
            {("label_override", "project")},
        ),
        (
            "label item accepted as proposed: no override",
            "human.decided",
            _decided("label", "accept", payload={"value": "human"}),
            task,
            set(),
        ),
        (
            "result rejected",
            "human.decided",
            _decided("result", "reject", reason="Tests are red", payload={"run_id": str(ITEM)}),
            task,
            {("result_rejected", "project")},
        ),
        (
            "result accepted",
            "human.decided",
            _decided("result", "accept", payload={"run_id": str(ITEM)}),
            task,
            {("result_accepted", "project")},
        ),
        (
            "approval approved",
            "human.decided",
            _decided(
                "approval", "approve", reason="Fine for staging", payload={"action_class": "deploy"}
            ),
            task,
            {("approval_decided", "project")},
        ),
        (
            "approval denied",
            "human.decided",
            _decided(
                "approval", "deny", reason="Not on Friday", payload={"action_class": "deploy"}
            ),
            task,
            {("approval_decided", "project")},
        ),
        (
            "question answered",
            "human.decided",
            _decided(
                "question", "answer", payload={"question": "Which logo?", "answer": "The round one"}
            ),
            task,
            {("question_answered", "project")},
        ),
        (
            "proposal accepted",
            "human.decided",
            _decided("proposal", "accept", payload={"task_id": str(TASK)}),
            task,
            {("proposal_accepted", "project")},
        ),
        (
            "proposal rejected: nothing",
            "human.decided",
            _decided("proposal", "reject"),
            task,
            set(),
        ),
        (
            "snoozed: not a decision yet",
            "human.decided",
            _decided("result", "snooze"),
            task,
            set(),
        ),
        (
            "unknown item kind",
            "human.decided",
            _decided("project_match", "accept"),
            task,
            set(),
        ),
        (
            "task created",
            "task.created",
            {
                "schema_version": 1,
                "task_id": str(TASK),
                "project_id": str(PROJECT),
                "label": "hybrid",
                "source": "user",
                "tainted": False,
                "doc": {"title": "Draft the logo"},
            },
            None,
            {("task_changed", "project")},
        ),
        (
            "status changed, not done",
            "task.status_changed",
            {
                "schema_version": 1,
                "task_id": str(TASK),
                "from": "today",
                "to": "in_progress",
                "actor": "user:x",
            },
            task,
            {("task_changed", "project")},
        ),
        (
            "done with an actual",
            "task.status_changed",
            {
                "schema_version": 1,
                "task_id": str(TASK),
                "from": "in_progress",
                "to": "done",
                "actor": "user:x",
            },
            _task(actual=75),
            {("task_changed", "project"), ("estimate_vs_actual", "project")},
        ),
        (
            "done without an actual",
            "task.status_changed",
            {
                "schema_version": 1,
                "task_id": str(TASK),
                "from": "backlog",
                "to": "done",
                "actor": "user:x",
            },
            _task(actual=None),
            {("task_changed", "project")},
        ),
        (
            "task updated: not a digest signal",
            "task.updated",
            {"schema_version": 1, "task_id": str(TASK), "changed_fields": ["title"], "doc": {}},
            task,
            set(),
        ),
        (
            "comment",
            "task.commented",
            {
                "schema_version": 1,
                "task_id": str(TASK),
                "project_id": str(PROJECT),
                "comment_id": str(ITEM),
                "author": "api_key:abc",
                "text": "Blocked on the font licence",
            },
            None,
            {("task_commented", "project")},
        ),
        (
            "project document changed",
            "document.changed",
            {**doc, "project_id": str(PROJECT)},
            None,
            {("document_changed", "project")},
        ),
        (
            "workspace knowledge base document added",
            "document.added",
            {**doc, "project_id": None},
            None,
            {("document_changed", "workspace")},
        ),
        (
            "context item linked",
            "context_item.linked",
            {
                "schema_version": 1,
                "context_item_id": str(ITEM),
                "task_id": str(TASK),
                "project_id": str(PROJECT),
                "target_type": "message",
                "target_id": str(uuid.uuid4()),
            },
            None,
            {("context_linked", "project")},
        ),
        (
            "focus response (P2-15)",
            "focus.responded",
            {"schema_version": 1, "task_id": str(TASK), "response": "done"},
            task,
            {("focus_response", "project")},
        ),
        (
            "focus setting changed (P2-15)",
            "focus.level_changed",
            {"schema_version": 1, "from": "gentle", "to": "firm", "scope": "workspace"},
            None,
            {("focus_setting_changed", "workspace")},
        ),
        (
            "an event the digest ignores",
            "project.created",
            {"project_id": str(PROJECT)},
            None,
            set(),
        ),
    ]


ALL_KINDS = {
    "label_override",
    "result_rejected",
    "result_accepted",
    "approval_decided",
    "question_answered",
    "estimate_vs_actual",
    "task_changed",
    "task_commented",
    "document_changed",
    "proposal_accepted",
    "context_linked",
    "focus_response",
    "focus_setting_changed",
}


@pytest.mark.req("FR-13.1")
@pytest.mark.wp("P2-03")
def test_classify_event_kinds() -> None:
    """T-P2-03-03
    Each event maps to the digest kinds of the plan's table and scope, nothing else; a
    project entry names its project (from the payload or the task's facts) and the
    workspace ones name none; together the cases reach every kind.
    """
    from tumnis.modules.agents.rules import (  # noqa: PLC0415
        DIGEST_KINDS,
        DigestEvent,
        classify_event,
    )

    assert set(DIGEST_KINDS) == ALL_KINDS
    seen: set[str] = set()
    for case, name, payload, task, expected in _cases():
        specs = classify_event(DigestEvent(name=name, payload=payload, actor="system", task=task))
        got = {(spec.kind, spec.scope) for spec in specs}
        assert got == expected, case
        assert len(specs) == len(got), case  # one entry per kind
        for spec in specs:
            if spec.scope == "project":
                assert spec.project_id == PROJECT, case
            else:
                assert spec.project_id is None, case
            assert isinstance(spec.data, dict), case
        seen |= {kind for kind, _scope in got}
    assert seen == ALL_KINDS


@pytest.mark.req("FR-13.1")
@pytest.mark.wp("P2-03")
def test_classify_event_data() -> None:
    """The entries carry what the plan's table names: the label's from and to, the rejected
    result's feedback, the approval's class and reason, estimate against actual, and
    whether a comment's author is a person (its text goes in an untrusted block when not).
    """
    from tumnis.modules.agents.rules import (  # noqa: PLC0415
        DigestEvent,
        classify_event,
    )

    def one(name: str, payload: dict[str, Any], task: Any = None, actor: str = "system") -> Any:
        [spec] = classify_event(DigestEvent(name=name, payload=payload, actor=actor, task=task))
        return spec

    label = one(
        "human.decided",
        _decided(
            "label_override",
            "ai",
            reason="It is research",
            previous={"label": "human"},
            payload={"value": "ai"},
        ),
        _task(),
    )
    assert label.data == {
        "task_id": str(TASK),
        "from": "human",
        "to": "ai",
        "reason": "It is research",
    }
    assert label.task_id == TASK
    rejected = one(
        "human.decided",
        _decided("result", "reject", reason="Tests are red", payload={"run_id": str(ITEM)}),
        _task(),
    )
    assert rejected.data["feedback"] == "Tests are red"
    assert rejected.data["run_id"] == str(ITEM)
    approval = one(
        "human.decided",
        _decided("approval", "approve", reason="Fine", payload={"action_class": "deploy"}),
        _task(),
    )
    assert approval.data == {
        "task_id": str(TASK),
        "action_class": "deploy",
        "decision": "approve",
        "reason": "Fine",
    }
    [actual, _changed] = sorted(
        classify_event(
            DigestEvent(
                name="task.status_changed",
                payload={
                    "task_id": str(TASK),
                    "from": "in_progress",
                    "to": "done",
                    "actor": "user:x",
                },
                actor="user:x",
                task=_task(actual=75, estimate=60),
            )
        ),
        key=lambda s: s.kind,
    )
    assert actual.kind == "estimate_vs_actual"
    assert actual.data == {"task_id": str(TASK), "estimate_minutes": 60, "actual_minutes": 75}
    by_agent = one(
        "task.commented",
        {
            "task_id": str(TASK),
            "project_id": str(PROJECT),
            "comment_id": str(ITEM),
            "author": "api_key:abc",
            "text": "hello",
        },
    )
    by_person = one(
        "task.commented",
        {
            "task_id": str(TASK),
            "project_id": str(PROJECT),
            "comment_id": str(ITEM),
            "author": "user:abc",
            "text": "hello",
        },
    )
    assert by_agent.data["author_kind"] == "api_key"
    assert by_agent.data["trusted"] is False
    assert by_person.data["author_kind"] == "user"
    assert by_person.data["trusted"] is True


@pytest.mark.req("FR-13.1")
@pytest.mark.wp("P2-03")
def test_digest_task_id_and_malformed_ids() -> None:
    """The subscriber looks up the task a decision targets (or its payload names) and the
    task of a status change; other events need no lookup. A missing or malformed id makes
    no entry rather than an error: an entry whose project is unknown is dropped."""
    from tumnis.modules.agents.rules import (  # noqa: PLC0415
        DigestEvent,
        classify_event,
        digest_task_id,
    )

    decided = _decided("result", "accept")
    assert digest_task_id("human.decided", decided) == TASK
    by_payload = {**decided, "target_type": "review_item", "payload": {"task_id": TASK}}
    assert digest_task_id("human.decided", by_payload) == TASK
    assert digest_task_id("human.decided", {**by_payload, "payload": None}) is None
    assert digest_task_id("task.status_changed", {"task_id": str(TASK)}) == TASK
    assert digest_task_id("focus.responded", {"task_id": "not-a-uuid"}) is None
    assert digest_task_id("task.commented", {"task_id": str(TASK)}) is None

    comment = {"task_id": str(TASK), "project_id": "", "author": "user:x", "text": "Hi"}
    assert classify_event(DigestEvent("task.commented", comment, "user:x")) == []
    malformed = {**comment, "project_id": "0192a000-not-a-uuid"}
    assert classify_event(DigestEvent("task.commented", malformed, "user:x")) == []


@pytest.mark.req("FR-13.1")
@pytest.mark.wp("P2-03")
def test_wait_decisions_take_facts_from_their_review_item() -> None:
    """P2-05's `human.decided` for a question or an approval carries only the decision
    body ({answer} or {reason}); the question's prompt and the approval's action class are
    on the review item. `with_review_item` fills them in, never over what the event says,
    and leaves other kinds alone."""
    from tumnis.modules.agents.rules import (  # noqa: PLC0415
        DigestEvent,
        classify_event,
        review_item_needed,
        with_review_item,
    )

    question = _decided("question", "answer", payload={"answer": "Teal"})
    approval = _decided("approval", "deny", reason="Not on Friday", payload={"reason": "x"})
    result = _decided("result", "accept")
    assert review_item_needed("human.decided", question) == ITEM
    assert review_item_needed("human.decided", approval) == ITEM
    assert review_item_needed("human.decided", result) is None
    assert review_item_needed("task.created", question) is None

    asked = with_review_item(question, {"prompt": "Which colour?", "choices": ["Teal"]})
    [entry] = classify_event(DigestEvent("human.decided", asked, "user:x", _task()))
    assert entry.data == {"task_id": str(TASK), "question": "Which colour?", "answer": "Teal"}

    gated = with_review_item(approval, {"action_class": "deploy_production", "rule": "r"})
    [entry] = classify_event(DigestEvent("human.decided", gated, "user:x", _task()))
    assert entry.data == {
        "task_id": str(TASK),
        "action_class": "deploy_production",
        "decision": "deny",
        "reason": "Not on Friday",
    }

    said = _decided("question", "answer", payload={"question": "Own words", "answer": "A"})
    assert with_review_item(said, {"prompt": "Stored"})["payload"]["question"] == "Own words"
    assert with_review_item(result, {"prompt": "Stored"}) == result
    assert with_review_item({**question, "payload": None}, {"prompt": "P"})["payload"] == {
        "question": "P"
    }
