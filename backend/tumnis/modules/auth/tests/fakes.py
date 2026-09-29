"""A test-only sign-in provider (P0-13, Hosted readiness): proves a provider plugs into
the auth module through `register_provider` without core changes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from tumnis.modules.auth.providers import InvalidCredentials, SignInResult

if TYPE_CHECKING:
    from tumnis.core.clock import Clock
    from tumnis.modules.auth.providers import DeploymentMode


@dataclass
class FakeProvider:
    """Signs in `user_id` of `workspace_id` when the credentials carry `token`."""

    user_id: UUID
    workspace_id: UUID
    token: str = "let-me-in"  # a test value, not a secret
    name: str = "fake"
    calls: list[dict[str, str]] = field(default_factory=list)

    def signup_allowed(self, mode: DeploymentMode) -> bool:
        return False

    async def authenticate(
        self, credentials: Mapping[str, str], *, source_ip: str, clock: Clock
    ) -> SignInResult:
        self.calls.append(dict(credentials))
        if credentials.get("token") != self.token:
            raise InvalidCredentials
        return SignInResult(user_id=self.user_id, workspace_id=self.workspace_id)
