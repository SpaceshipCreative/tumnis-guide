"""A stuck run's report, reviewed (P4-02, FR-10.5, Scott decision 73).

- `stuck_requests.state` gains `done` (the person accepted the step the agent took itself,
  and carries on with the task) and `reopened` (the person rejected it: the step is theirs
  again, and the reason is a comment on the task). The check is re-added NOT VALID: every
  new or changed row is checked, and no scan of `stuck_requests` runs inside the
  migration's transaction (squawk's constraint-missing-not-valid). Every existing row
  already holds one of the older states, all still allowed.
- Downgrade puts `done` and `reopened` rows back to `took_step` (the agent's report, as
  before the review) so the older check holds.
"""

from alembic import op

revision = "agents_0010"
down_revision = "agents_0009"
branch_labels = None
depends_on = None
phase = "expand"

STATES_V1 = "'working', 'split', 'took_step', 'fallback'"
STATES_V2 = f"{STATES_V1}, 'done', 'reopened'"


def upgrade() -> None:
    op.drop_constraint("ck_stuck_requests_state", "stuck_requests", type_="check")
    op.execute(
        "ALTER TABLE stuck_requests ADD CONSTRAINT ck_stuck_requests_state"
        f" CHECK (state IN ({STATES_V2})) NOT VALID"
    )


def downgrade() -> None:
    op.execute("UPDATE stuck_requests SET state = 'took_step' WHERE state IN ('done', 'reopened')")
    op.drop_constraint("ck_stuck_requests_state", "stuck_requests", type_="check")
    op.execute(
        "ALTER TABLE stuck_requests ADD CONSTRAINT ck_stuck_requests_state"
        f" CHECK (state IN ({STATES_V1})) NOT VALID"
    )
