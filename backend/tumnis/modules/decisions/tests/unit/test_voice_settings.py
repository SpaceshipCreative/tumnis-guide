"""Voice settings (P4-03, FR-10.8, FR-11.7, Data flow rule 6): voice is opt-in per focus
level, the browser's own speech synthesis is the zero-config engine, and hosted speech is
off by default and never used for a local-only project."""

import importlib

import pytest


@pytest.mark.req("FR-11.7", "FR-10.8")
@pytest.mark.wp("P4-03")
def test_hosted_speech_off_by_default() -> None:
    """T-P4-03-07
    Defaults: no level speaks, the engine is the browser's, the server provider is Piper
    and hosted speech is not allowed, so nothing goes to a server. A hosted provider is used
    only once allowed, and never for a local-only project (its text stays on the device).
    """
    rules = importlib.import_module("tumnis.modules.decisions.rules")
    VoiceSettings, speech_route = rules.VoiceSettings, rules.speech_route  # noqa: N806

    default = VoiceSettings()
    assert default.enabled_levels == set()
    assert default.engine == "browser"
    assert default.server_provider == "piper"
    assert default.hosted_allowed is False
    assert speech_route(default, local_only=False) == "browser"

    piper = VoiceSettings(enabled_levels={"nudge"}, engine="server")
    assert speech_route(piper, local_only=False) == "piper"
    assert speech_route(piper, local_only=True) == "piper"  # Piper is local

    hosted_off = VoiceSettings(engine="server", server_provider="hosted")
    assert speech_route(hosted_off, local_only=False) == "browser"  # not allowed yet

    hosted = VoiceSettings(engine="server", server_provider="hosted", hosted_allowed=True)
    assert speech_route(hosted, local_only=False) == "hosted"
    assert speech_route(hosted, local_only=True) == "browser"  # refused: local-only project
