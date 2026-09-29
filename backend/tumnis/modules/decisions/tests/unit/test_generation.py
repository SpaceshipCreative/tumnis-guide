"""The Generation slot (P1-03, FR-11.8): the placeholder first action over the fake, its
one-line cleanup, its timeout and the two fields it sends."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from uuid import UUID

import pytest
from structlog.testing import capture_logs

from tumnis.core.adapters.errors import AdapterUnavailable
from tumnis.core.adapters.registry import Health
from tumnis.modules.decisions import generation_config
from tumnis.modules.decisions.adapters.fake import FakeGeneration
from tumnis.modules.decisions.api import configure_generation
from tumnis.modules.decisions.generation_api import (
    MAX_PLACEHOLDER_CHARS,
    SPOKEN_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    placeholder_first_action,
    spoken_focus_message,
)
from tumnis.settings import GenerationSettings

PROJECT_ID = UUID("01926f00-0000-7000-8000-00000000a0c1")
TITLE = "Sign the Acme master services agreement"
PROJECT = "Acme site"


@pytest.fixture(autouse=True)
def _restore_slot() -> Iterator[None]:
    before = generation_config.settings()
    yield
    generation_config.configure(before)


def use(fake: FakeGeneration, **settings: int) -> None:
    configure_generation(GenerationSettings.model_validate(settings), provider=fake)


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
async def test_placeholder_comes_from_fake() -> None:
    """T-P1-03-01
    Scripted fake text is returned trimmed to one line and at most 120 characters.
    """
    use(FakeGeneration(text="  Draft the outline of the Acme agreement.\nThen send it to legal.\n"))
    first = await placeholder_first_action(title=TITLE, project_name=PROJECT, project_id=PROJECT_ID)
    assert first == "Draft the outline of the Acme agreement."

    long_line = "Read " + " ".join(["every clause of the agreement"] * 12) + "."
    use(FakeGeneration(text=long_line))
    first = await placeholder_first_action(title=TITLE, project_name=PROJECT, project_id=PROJECT_ID)
    assert first is not None
    assert "\n" not in first
    assert 0 < len(first) <= MAX_PLACEHOLDER_CHARS
    assert long_line.startswith(first)


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
async def test_timeout_returns_none() -> None:
    """T-P1-03-02
    With the timeout set to 50 ms for the test (`generation.placeholder_timeout_ms`, 2,000 ms
    in production, configurable per R-30), a fake that sleeps 200 ms yields None, no
    exception.
    """
    assert GenerationSettings().placeholder_timeout_ms == 2000
    fake = FakeGeneration(text="Open the agreement.", delay_ms=200)
    use(fake, placeholder_timeout_ms=50)

    started = time.monotonic()
    first = await placeholder_first_action(title=TITLE, project_name=PROJECT, project_id=PROJECT_ID)
    elapsed = time.monotonic() - started

    assert first is None
    assert elapsed < 0.19
    assert [call.timeout_ms for call in fake.calls] == [50]


@pytest.mark.req("Data flow rule 6")
@pytest.mark.wp("P1-03")
async def test_only_title_and_project_name_sent() -> None:
    """T-P1-03-03
    The fake's captured prompt holds the title and project name and nothing else from the
    task: the fixed system prompt, the two fields under their labels, capped at 300 and
    120 characters.
    """
    fake = FakeGeneration(text="Open the agreement.")
    use(fake)
    await placeholder_first_action(title=TITLE, project_name=PROJECT, project_id=PROJECT_ID)

    (call,) = fake.calls
    assert call.system == SYSTEM_PROMPT
    assert TITLE in call.user
    assert PROJECT in call.user
    assert str(PROJECT_ID) not in call.user + call.system
    rest = call.user.replace(TITLE, "").replace(PROJECT, "")
    assert set(rest.split()) <= {"Task:", "Project:"}

    fake.calls.clear()
    await placeholder_first_action(title="t" * 400, project_name="p" * 200, project_id=PROJECT_ID)
    (call,) = fake.calls
    assert "t" * 300 in call.user
    assert "t" * 301 not in call.user
    assert "p" * 120 in call.user
    assert "p" * 121 not in call.user


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
@pytest.mark.parametrize("text", ["", "   ", "\n\n", "...", "?!", " - \n...\n** **"])
async def test_multiline_or_empty_output_rejected(text: str) -> None:
    """T-P1-03-06
    Empty output and output that is only punctuation return None.
    """
    fake = FakeGeneration(text=text)
    use(fake)
    first = await placeholder_first_action(title=TITLE, project_name=PROJECT, project_id=PROJECT_ID)
    assert first is None
    assert len(fake.calls) == 1


class _Hanging:
    """A provider that ignores its timeout: only the caller's own bound stops it."""

    async def complete(self, *, system: str, user: str, max_tokens: int, timeout_ms: int) -> str:
        await asyncio.sleep(5)
        return "too late"

    async def health(self) -> Health:
        return "ok"


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
async def test_a_provider_that_hangs_is_cut_off_at_the_timeout() -> None:
    """The slot bounds the call itself: a provider that never honors `timeout_ms` still
    yields None at the configured timeout, and the log line names the purpose and the
    project, never the prompt."""
    configure_generation(GenerationSettings(placeholder_timeout_ms=50), provider=_Hanging())

    started = time.monotonic()
    with capture_logs() as logs:
        first = await placeholder_first_action(
            title=TITLE, project_name=PROJECT, project_id=PROJECT_ID
        )
    assert first is None
    assert time.monotonic() - started < 1.0
    (line,) = [log for log in logs if log["event"] == "decisions.generation_skipped"]
    assert line["purpose"] == "placeholder"
    assert line["project_id"] == str(PROJECT_ID)
    assert line["error"] == "TimeoutError"
    assert TITLE not in repr(line)
    assert PROJECT not in repr(line)


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
async def test_a_failing_provider_or_no_endpoint_yields_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An adapter error gives None, not an exception; a slot with no provider (no local
    endpoint configured) gives None without a call."""
    monkeypatch.setenv("TUMNIS_ADAPTERS", "real")
    fake = FakeGeneration(
        fail=AdapterUnavailable("decisions.vllm_generation", "chat_completion", "down")
    )
    use(fake)
    assert (
        await placeholder_first_action(title=TITLE, project_name=PROJECT, project_id=PROJECT_ID)
        is None
    )
    assert len(fake.calls) == 1

    configure_generation(GenerationSettings())
    assert (
        await placeholder_first_action(title=TITLE, project_name=PROJECT, project_id=PROJECT_ID)
        is None
    )


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
async def test_spoken_focus_message_sends_only_the_text_and_keeps_one_sentence() -> None:
    """The spoken form (P2-16's caller) sends the fixed spoken prompt and the message text
    (capped at 500), and keeps one sentence of at most 200 characters; past
    `generation.spoken_timeout_ms` it is None."""
    message = "Focus: Review the Acme agreement (45 min) before the 2 pm call."
    fake = FakeGeneration(text="Review the Acme agreement before your two o'clock call.\nGo.")
    use(fake)
    spoken = await spoken_focus_message(text=message, project_id=PROJECT_ID)
    assert spoken == "Review the Acme agreement before your two o'clock call."
    (call,) = fake.calls
    assert (call.system, call.user) == (SPOKEN_SYSTEM_PROMPT, message)

    fake.calls.clear()
    await spoken_focus_message(text="m" * 600, project_id=PROJECT_ID)
    assert fake.calls[0].user == "m" * 500

    use(FakeGeneration(text="Too slow.", delay_ms=200), spoken_timeout_ms=50)
    assert await spoken_focus_message(text=message, project_id=PROJECT_ID) is None


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
def test_slot_builds_its_provider_once_from_the_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configured without a provider, the slot builds one on first use through the adapter
    registry and keeps it: the fake under TUMNIS_ADAPTERS=fake; in real mode the vLLM
    adapter for the configured endpoint and model, or none when either is unset."""
    from tumnis.core.net import NetPolicy  # noqa: PLC0415

    monkeypatch.setenv("TUMNIS_ADAPTERS", "fake")
    configure_generation(GenerationSettings())
    built = generation_config.provider()
    assert isinstance(built, FakeGeneration)
    assert generation_config.provider() is built

    monkeypatch.setenv("TUMNIS_ADAPTERS", "real")
    for partial in ({"base_url": "http://vllm.example.org:8000"}, {"model": "m"}):
        configure_generation(GenerationSettings.model_validate(partial))
        assert generation_config.provider() is None

    configure_generation(
        GenerationSettings(base_url="http://vllm.example.org:8000", model="Qwen/Qwen2.5-3B"),
        net_policy=NetPolicy(mode="hosted"),
    )
    real = generation_config.provider()
    assert type(real).__name__ == "VllmGeneration"
    assert generation_config.provider() is real


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
def test_worker_configures_the_slot_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GENERATION__BASE_URL, GENERATION__MODEL and GENERATION__PLACEHOLDER_TIMEOUT_MS set
    the deployment's slot (R-30: the timeout is configurable); the worker hands them to
    the slot at start."""
    from tumnis.settings import Settings  # noqa: PLC0415
    from tumnis.worker import configure_generation as worker_configure  # noqa: PLC0415

    monkeypatch.setenv("GENERATION__BASE_URL", "http://vllm.example.org:8000")
    monkeypatch.setenv("GENERATION__MODEL", "Qwen/Qwen2.5-3B-Instruct")
    monkeypatch.setenv("GENERATION__PLACEHOLDER_TIMEOUT_MS", "1500")
    settings = Settings(database_url="postgresql://x/y", database_direct_url="postgresql://x/y")

    worker_configure(settings)

    assert generation_config.settings() == GenerationSettings(
        base_url="http://vllm.example.org:8000",
        model="Qwen/Qwen2.5-3B-Instruct",
        placeholder_timeout_ms=1500,
    )
