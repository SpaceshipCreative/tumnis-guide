"""The four errors every adapter speaks (P0-09); `tumnis.core.adapters.base` re-exports them.

Concrete adapters translate their library's exceptions into these inside the function they
pass to `Adapter.call`, so the base never needs to know about httpx or aioboto3. They live
in their own module so the breaker can raise `CircuitOpen` without importing the base.
"""

# The names are the plan's shared contract (P0-09 interfaces), not "...Error".
# ruff: noqa: N818


class AdapterError(Exception):
    """An outside dependency failed; `retryable` says whether another attempt may succeed."""

    def __init__(
        self,
        adapter: str,
        op: str,
        message: str,
        *,
        retryable: bool,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(f"{adapter}.{op}: {message}")
        self.adapter = adapter
        self.op = op
        self.message = message
        self.retryable = retryable
        self.retry_after_s = retry_after_s


class AdapterTimeout(AdapterError):
    """The call ran past `CallPolicy.timeout_s`. Retryable."""

    def __init__(
        self,
        adapter: str,
        op: str,
        message: str = "timed out",
        *,
        retryable: bool = True,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(adapter, op, message, retryable=retryable, retry_after_s=retry_after_s)


class CircuitOpen(AdapterError):
    """The breaker refused the call; nothing was sent. Not retryable: fail fast."""

    def __init__(
        self,
        adapter: str,
        op: str,
        message: str = "circuit open",
        *,
        retryable: bool = False,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(adapter, op, message, retryable=retryable, retry_after_s=retry_after_s)


class AdapterUnavailable(AdapterError):
    """Connection refused, 5xx, 429. Retryable; counts against the breaker."""

    def __init__(
        self,
        adapter: str,
        op: str,
        message: str,
        *,
        retryable: bool = True,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(adapter, op, message, retryable=retryable, retry_after_s=retry_after_s)


class AdapterRejected(AdapterError):
    """4xx or validation: the provider answered and said no. Not retryable, and not an
    outage, so it does not count against the breaker."""

    def __init__(
        self,
        adapter: str,
        op: str,
        message: str,
        *,
        retryable: bool = False,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(adapter, op, message, retryable=retryable, retry_after_s=retry_after_s)
