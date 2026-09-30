"""The webhook signature check (P2-13, SEC-5).

GitHub signs each delivery: `X-Hub-Signature-256: sha256=<hex>`, the HMAC-SHA256 of the raw
request body under the webhook secret
([validating deliveries](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)).
Pure, but kept out of `rules.py`: the rules allow-list has no `hmac` or `hashlib`.
"""

import hashlib
import hmac
import re
from typing import Final

_PREFIX: Final = "sha256="
_HEX_DIGEST: Final = re.compile(r"[0-9a-fA-F]{64}")


def verify_signature(secret: bytes, body: bytes, header: str | None) -> bool:
    """True when `header` is `sha256=<hex>` of HMAC-SHA256 over `body` under `secret`. An
    empty secret, a missing or malformed header and any other algorithm are refused; the
    digests are compared in constant time."""
    if not secret or not header or not header.startswith(_PREFIX):
        return False
    given = header.removeprefix(_PREFIX)
    if _HEX_DIGEST.fullmatch(given) is None:
        return False
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given.lower())
