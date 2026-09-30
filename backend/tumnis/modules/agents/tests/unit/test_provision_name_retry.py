"""choose_name_step retries a profile-name collision (P1-06, FR-5.10): two projects whose
names normalise alike may pick the same free name at once; the loser's insert breaks the
unique name index, and the step runs again against the committed names."""

import pytest
from sqlalchemy.exc import IntegrityError

from tumnis.modules.agents import workflows


def _integrity(constraint: str) -> IntegrityError:
    orig = Exception(f'duplicate key value violates unique constraint "{constraint}"')
    return IntegrityError("INSERT INTO agent_profiles ...", {}, orig)


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P1-06")
def test_only_a_name_collision_is_retried() -> None:
    assert workflows.name_collision(_integrity("ux_agent_profiles_ws_name"))
    assert not workflows.name_collision(_integrity("ux_agent_profiles_one_project_agent"))
    assert not workflows.name_collision(RuntimeError("ux_agent_profiles_ws_name"))
