"""Sign-in providers and second factors (P0-13, Hosted readiness). Spec skeleton."""

from collections.abc import Mapping
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

from tumnis.core.clock import Clock

DeploymentMode = Literal["self-hosted", "hosted"]


class InvalidCredentials(Exception):  # noqa: N818  # the plan's name
    """The credentials do not sign anyone in."""


class LockedOut(Exception):  # noqa: N818  # the plan's name
    """Too many failures: try again after `retry_after_s`."""


class SignInResult(BaseModel):
    user_id: UUID
    workspace_id: UUID
    needs_second_factor: bool = True  # always True for local password (SEC-1)


class SignInProvider(Protocol):
    name: str

    def signup_allowed(self, mode: DeploymentMode) -> bool: ...

    async def authenticate(
        self, credentials: Mapping[str, str], *, source_ip: str, clock: Clock
    ) -> SignInResult: ...


def register_provider(p: SignInProvider) -> None:
    raise NotImplementedError("P0-13")


def unregister_provider(name: str) -> None:
    raise NotImplementedError("P0-13")
