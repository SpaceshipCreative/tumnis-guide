"""The Coolify status port's contract (P2-14, FR-12.2): the fake and the real client over
the recorded HTTP answers behave the same (AGENTS.md: fakes obey the real contract)."""

from __future__ import annotations

import pytest

from tumnis.core.adapters.contract import AdapterContract
from tumnis.core.adapters.errors import AdapterRejected
from tumnis.modules.coolify.adapters.fake import FakeCoolifyStatus
from tumnis.modules.coolify.adapters.port import CoolifyStatus
from tumnis.modules.coolify.tests.replay import Replay, load, recorded_api, recording_names

PORTAL = "dune0portal0example00004"  # the previews recording


class CoolifyStatusContract(AdapterContract[CoolifyStatus]):
    port, adapter_name = CoolifyStatus, "coolify.status"

    async def test_every_recorded_application_reads_back(self, subject: CoolifyStatus) -> None:
        for name in recording_names():
            recorded = load(name)["responses"]["application"]
            app = await subject.get_application(recorded["uuid"])
            assert (app.uuid, app.name, app.fqdn) == (
                recorded["uuid"],
                recorded["name"],
                recorded["fqdn"],
            )
            assert app.preview_url_template == "{{pr_id}}.{{domain}}"

    async def test_deployments_come_newest_first_up_to_take(self, subject: CoolifyStatus) -> None:
        every = await subject.list_deployments(PORTAL)
        assert [d.deployment_uuid for d in every] == [
            "dpl00640example0000000",
            "dpl00636example0000000",
            "dpl00633example0000000",
            "dpl00630example0000000",
        ]
        assert [d.pull_request_id for d in every] == [14, 0, 14, 11]
        two = await subject.list_deployments(PORTAL, take=2)
        assert two == every[:2]

    async def test_a_running_deployment_has_no_finished_time(self, subject: CoolifyStatus) -> None:
        (running, done) = await subject.list_deployments("cove0web0example00000003")
        assert (running.status, running.finished_at, running.commit) == (
            "in_progress",
            None,
            "HEAD",
        )
        assert done.finished_at is not None

    async def test_unknown_application_is_rejected(self, subject: CoolifyStatus) -> None:
        with pytest.raises(AdapterRejected):
            await subject.get_application("nope0example000000000000")
        with pytest.raises(AdapterRejected):
            await subject.list_deployments("nope0example000000000000")

    async def test_health_is_ok(self, subject: CoolifyStatus) -> None:
        assert await subject.health() == "ok"


@pytest.mark.contract
@pytest.mark.req("FR-12.2")
@pytest.mark.wp("P2-14")
class TestCoolifyStatusFake(CoolifyStatusContract):
    impl = "fake"

    @pytest.fixture
    def subject(self) -> CoolifyStatus:
        return FakeCoolifyStatus()


@pytest.mark.contract
@pytest.mark.req("FR-12.2")
@pytest.mark.wp("P2-14")
class TestCoolifyStatusRecorded(CoolifyStatusContract):
    impl = "recorded"

    @pytest.fixture
    def subject(self) -> CoolifyStatus:
        return recorded_api(Replay(*(load(name) for name in recording_names())))
