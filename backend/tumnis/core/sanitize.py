"""One allow-list HTML sanitizer (P0-16, SEC-4). Interface only; the code lands with its
spec tests (T-P0-16-03 to 05)."""


def sanitize_html(html: str) -> str:
    raise NotImplementedError


def to_plain_text(html: str) -> str:
    raise NotImplementedError
