"""The fake-script store's process switch and parser registry (R-37)."""

from __future__ import annotations

from typing import Any

import pytest

from tumnis.core import fake_scripts


def _parse(body: Any) -> tuple[str, dict[str, Any]]:
    return "", dict(body)


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
async def test_lookup_reads_nothing_while_the_store_is_disabled() -> None:
    """T-P0-04-27
    A process that never enabled the store (unit tests, real adapters) gets no script and
    opens no connection: the sockets of this layer are disabled.
    """
    assert not fake_scripts.enabled()
    assert await fake_scripts.lookup("decisions.jev", "quick_add_label") is None


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
def test_a_hook_name_registers_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-P0-04-28
    Each hook name has one parser, like the adapter registry: a second registration of
    the same name is refused, and an unknown name has none.
    """
    monkeypatch.setattr(fake_scripts, "_PARSERS", {})
    fake_scripts.register_fake_script("demo.fake", _parse)
    assert fake_scripts.parser("demo.fake") is _parse
    assert fake_scripts.parser("other.fake") is None
    with pytest.raises(ValueError, match=r"demo\.fake"):
        fake_scripts.register_fake_script("demo.fake", _parse)
