"""The settings section registry behind GET/PUT /v1/settings/{section} (P0-26, SEC-6)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel


class _Token(BaseModel):
    token: str = ""
    label: str = ""


class _Other(BaseModel):
    value: int = 0


@pytest.mark.req("SEC-6")
@pytest.mark.wp("P0-26")
@pytest.mark.xfail(strict=True, reason="spec:P0-26")
def test_register_section_refuses_reserved_duplicate_and_unknown_secret_names() -> None:
    """T-P0-26-11
    `workspace` and `modules` have routes of their own and cannot be sections; a name taken
    by another model is refused; registering the same section again is a no-op; a secret
    field must be a field of the model.
    """
    from tumnis.core.settings_store import (  # noqa: PLC0415
        SettingSection,
        register_section,
        registered_section,
    )

    for reserved in ("workspace", "modules"):
        with pytest.raises(ValueError, match=reserved):
            register_section(SettingSection(reserved, _Token))

    first = SettingSection("test.registry_token", _Token, secret_fields=frozenset({"token"}))
    register_section(first)
    register_section(first)
    assert registered_section("test.registry_token") is first
    assert registered_section("test.never_registered") is None

    with pytest.raises(ValueError, match=r"test\.registry_token"):
        register_section(SettingSection("test.registry_token", _Other))
    with pytest.raises(ValueError, match="nope"):
        register_section(
            SettingSection("test.registry_bad", _Token, secret_fields=frozenset({"nope"}))
        )
