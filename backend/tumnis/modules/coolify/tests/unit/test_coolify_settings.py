"""The Coolify settings section (P2-14): Settings > Coolify holds the API's base URL and a
read-only token; the token is a secret (write-only over HTTP)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


@pytest.mark.req("FR-12.2")
@pytest.mark.wp("P2-14")
def test_settings_section_keeps_the_token_secret() -> None:
    from tumnis.core.settings_store import registered_section  # noqa: PLC0415
    from tumnis.modules.coolify.api import CoolifySettings  # noqa: PLC0415

    section = registered_section("coolify")
    assert section is not None
    assert section.model is CoolifySettings
    assert section.secret_fields == frozenset({"token"})

    assert not CoolifySettings().configured
    settings = CoolifySettings(base_url=" https://coolify.example.com/ ", token="t")
    assert settings.base_url == "https://coolify.example.com"
    assert settings.configured
    assert CoolifySettings(base_url="http://192.168.50.10:8000").configured is False
    with pytest.raises(ValidationError):
        CoolifySettings(base_url="coolify.example.com")
    with pytest.raises(ValidationError):
        CoolifySettings(base_url="ftp://coolify.example.com")
