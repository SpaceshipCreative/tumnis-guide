"""The outbound payload builder (P1-01, Data flow rule 6): only whitelisted, capped fields
leave the server, never attachments, and never a request larger than Jev accepts."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from tumnis.modules.decisions.tests._cases import POINTS, stored_inputs

HOSTILE_KEYS = ("attachments", "body", "html", "raw", "headers", "id")
extra_values = st.one_of(st.text(max_size=50), st.binary(max_size=50), st.integers())


def flatten_keys(state: dict[str, Any]) -> Iterator[tuple[str, str | None]]:
    """(field, None) for every top-level key, (field, inner) for keys inside record lists."""
    for key, value in state.items():
        yield key, None
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    for inner in item:
                        yield key, inner


def all_values(value: Any) -> Iterator[Any]:
    if isinstance(value, dict):
        for inner in value.values():
            yield from all_values(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from all_values(inner)
    else:
        yield value


@pytest.mark.req("Data flow rule 6")
@pytest.mark.wp("P1-01")
@pytest.mark.xfail(strict=True, reason="spec:P1-01")
@settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
@given(
    point=st.sampled_from(sorted(POINTS)),
    extra=st.dictionaries(st.text(max_size=20), extra_values, max_size=8),
    hostile=st.dictionaries(st.sampled_from(HOSTILE_KEYS), extra_values),
)
@example(point="project_match", extra={"attachments": b"%PDF-1.7"}, hostile={"body": "x" * 50})
def test_only_whitelisted_fields_are_sent(
    point: str, extra: dict[str, Any], hostile: dict[str, Any]
) -> None:
    """T-P1-01-04
    Random extra keys (including `attachments`, `body`, `html` and bytes values) merged
    into valid inputs never reach the request: its state holds only whitelisted keys (and
    whitelisted keys inside record lists), no value is bytes, and `fields_sent` names
    exactly the state's keys, sorted.
    """
    from tumnis.modules.decisions.catalog import (  # noqa: PLC0415
        CATALOGUE,
        DecisionPoint,
        build_request,
    )

    spec = CATALOGUE[DecisionPoint(point)]
    valid = stored_inputs(point)
    noise = {k: v for k, v in {**extra, **hostile}.items() if k not in spec.fields}
    req = build_request(DecisionPoint(point), {**noise, **valid})

    for field, inner in flatten_keys(req.state):
        assert field in spec.fields, field
        if inner is not None:
            item_fields = spec.fields[field].item_fields
            assert item_fields is not None
            assert inner in item_fields, (field, inner)
    assert not any(isinstance(v, bytes | bytearray) for v in all_values(req.state))
    assert req.fields_sent == tuple(sorted(req.state))
    assert req.point is DecisionPoint(point)


@pytest.mark.req("Data flow rule 6")
@pytest.mark.wp("P1-01")
@pytest.mark.xfail(strict=True, reason="spec:P1-01")
@settings(max_examples=80, suppress_health_check=[HealthCheck.too_slow])
@given(body=st.text(max_size=12_000))
@example(body="a" * 2_001)
@example(body="é" * 5_000)  # decomposed: "e" + U+0301, NFC composes it to one code point
@example(body="日本語のメール本文" * 800)
@example(body="\U0001f600" * 3_000)
def test_project_match_body_capped_at_2000_chars(body: str) -> None:
    """T-P1-01-05
    Whatever the body's length and script, the `body_head` sent is at most 2,000
    characters, is NFC-normalized, and is a prefix of the normalized body.
    """
    import unicodedata  # noqa: PLC0415

    from tumnis.modules.decisions.catalog import DecisionPoint, build_request  # noqa: PLC0415

    for point in (DecisionPoint.PROJECT_MATCH, DecisionPoint.ACTIONABILITY):
        req = build_request(point, {**stored_inputs(point.value), "body_head": body})
        sent = req.state.get("body_head", "")
        assert len(sent) <= 2_000
        normalized = unicodedata.normalize("NFC", body)
        assert normalized.startswith(sent)
        assert sent == normalized[: len(sent)]
        if normalized:
            assert sent == normalized[:2_000]


@pytest.mark.req("Data flow rule 6")
@pytest.mark.wp("P1-01")
@pytest.mark.xfail(strict=True, reason="spec:P1-01")
@pytest.mark.parametrize("point", sorted(POINTS))
def test_attachments_never_sent(point: str) -> None:
    """T-P1-01-06
    An input carrying attachments (names, content, base64) and the full HTML body produces
    no trace of them anywhere in the serialized request.
    """
    from tumnis.modules.decisions.catalog import DecisionPoint, build_request  # noqa: PLC0415

    marker = "ZmFrZS1pbnZvaWNlLWJ5dGVz"  # base64 of the attachment bytes below
    inputs = {
        **stored_inputs(point),
        "attachments": [
            {"filename": "invoice-7731.pdf", "content": b"fake-invoice-bytes", "b64": marker}
        ],
        "html": "<html><body><p>secret-html-body-9f2</p></body></html>",
        "body": "full-body-text-4c1 " * 200,
    }
    req = build_request(DecisionPoint(point), inputs)
    wire = json.dumps(req.model_dump(mode="json"), sort_keys=True)
    for trace in ("attachments", "invoice-7731.pdf", marker, "secret-html-body-9f2", "4c1"):
        assert trace not in wire, trace


@pytest.mark.req("FR-11.9")
@pytest.mark.wp("P1-01")
@pytest.mark.xfail(strict=True, reason="spec:P1-01")
def test_oversized_request_is_refused() -> None:
    """T-P1-01-13
    A project match over 254 long projects estimates above MAX_REQUEST_TOKENS and raises
    DecisionRequestTooLarge from build_request, before any provider is involved; the
    stored inputs estimate well under it.
    """
    from tumnis.modules.decisions.catalog import (  # noqa: PLC0415
        MAX_REQUEST_TOKENS,
        DecisionPoint,
        DecisionRequestTooLarge,
        build_request,
        estimate_tokens,
    )

    big = [
        {
            "name": "N" * 500,
            "client": "C" * 500,
            "goal": "G" * 500,
            "domains": [f"d{n}.example.com" * 20 for n in range(30)],
            "people": [f"person{n}@example.com" * 12 for n in range(30)],
            "keywords": ["k" * 200 for _ in range(30)],
        }
        for _ in range(254)
    ]
    inputs = {**stored_inputs("project_match"), "projects": big}
    with pytest.raises(DecisionRequestTooLarge) as refused:
        build_request(DecisionPoint.PROJECT_MATCH, inputs)
    assert refused.value.estimated_tokens > MAX_REQUEST_TOKENS

    for point in POINTS:
        req = build_request(DecisionPoint(point), stored_inputs(point))
        assert 0 < estimate_tokens(req) < MAX_REQUEST_TOKENS // 10
