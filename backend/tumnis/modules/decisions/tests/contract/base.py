"""`DecisionsProviderContract`: the cases every decisions provider passes (P1-01), run
against the fake and against `JevDecisions` replaying the recorded Jev answers.

Also the replay transport the Jev side uses: an `httpx2.MockTransport` that fails the test
unless the request body the SDK sends equals a recording's `request`, and answers with that
recording's `response`.
"""

from __future__ import annotations

import json
import math
from typing import Any

import httpx2

from tumnis.core.adapters.contract import AdapterContract
from tumnis.modules.decisions.adapters.port import DecisionsProvider
from tumnis.modules.decisions.tests._cases import PINNED_MODEL, answered_recordings

Recording = dict[str, Any]


def _canonical(body: Any) -> str:
    return json.dumps(body, sort_keys=True, ensure_ascii=False)


def replay_transport(
    recordings: list[Recording], seen: list[dict[str, Any]] | None = None
) -> httpx2.MockTransport:
    """Answers each POST /v1/systemone with the recording whose `request` equals the body
    sent; any other request fails the test. Every body sent is appended to `seen`."""
    by_body = {_canonical(rec["request"]): rec for rec in recordings}

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/systemone"
        body = json.loads(request.content)
        if seen is not None:
            seen.append(body)
        rec = by_body.get(_canonical(body))
        assert rec is not None, f"no recording for the request sent: {_canonical(body)[:400]}"
        response = rec["response"]
        return httpx2.Response(
            response["status"], json=response["body"], headers=response.get("headers", {})
        )

    return httpx2.MockTransport(handler)


def _sums_to_one(probabilities: dict[str, float]) -> bool:
    return math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-6)


class DecisionsProviderContract(AdapterContract[DecisionsProvider]):
    port, adapter_name = DecisionsProvider, "decisions.jev"

    async def test_every_point_answers_every_question_typed(
        self, subject: DecisionsProvider
    ) -> None:
        """T-P1-01-08 (fake) and T-P1-01-09 (Jev replaying recordings)
        For every recorded case (all nine points), asking the built request answers every
        question with its own primitive: a Choice picks one of its options with
        probabilities over exactly those options summing to 1; a Score has probabilities
        over its level indices summing to 1 and an expected score inside the levels; a
        Noul is a probability of yes. The answering model is the pinned versioned id.
        """
        from tumnis.modules.decisions.catalog import (  # noqa: PLC0415
            CATALOGUE,
            DecisionPoint,
            build_request,
        )

        cases = answered_recordings()
        assert {rec["point"] for _, rec in cases} == {p.value for p in DecisionPoint}
        for name, rec in cases:
            req = build_request(DecisionPoint(rec["point"]), rec["inputs"])
            resp = await subject.ask(
                req, model=PINNED_MODEL, timeout_ms=CATALOGUE[req.point].timeout_ms
            )
            assert set(resp.answers) == set(req.questions), name
            assert resp.provider == subject.provider
            assert resp.model == PINNED_MODEL, name
            assert resp.input_tokens >= 0
            assert resp.latency_ms >= 0
            for qid, question in req.questions.items():
                answer = resp.answers[qid]
                assert answer.type == question.type, (name, qid)
                if answer.type == "choice":
                    assert question.type == "choice"
                    assert answer.choice in question.criteria, (name, qid)
                    assert set(answer.probabilities) == set(question.criteria), (name, qid)
                    assert _sums_to_one(answer.probabilities), (name, qid)
                    assert answer.probabilities[answer.choice] == max(answer.probabilities.values())
                    assert 0.0 <= answer.confidence <= 1.0
                elif answer.type == "score":
                    assert question.type == "score"
                    levels = {str(n) for n in range(len(question.criteria))}
                    assert set(answer.probabilities) == levels, (name, qid)
                    assert _sums_to_one(answer.probabilities), (name, qid)
                    assert 0.0 <= answer.score <= len(question.criteria) - 1
                    assert 0.0 <= answer.confidence <= 1.0
                else:
                    assert 0.0 <= answer.noul <= 1.0, (name, qid)

    async def test_health_is_ok_when_fresh(self, subject: DecisionsProvider) -> None:
        """T-P1-01-08 (fake) and T-P1-01-09 (Jev replaying recordings)
        A provider that has not failed reports "ok".
        """
        assert await subject.health() == "ok"
