"""The fake storage passes the shared storage contract (P1-14, FR-15.7). One fake stands in
for every storage adapter, so it is checked under each adapter name it is registered for."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.modules.knowledge.tests.contract.storage_contract import StorageContract

if TYPE_CHECKING:
    from tests.fixtures import Fakes
    from tumnis.modules.knowledge.storage import StorageBackend


@pytest.mark.contract
@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P1-14")
class TestFakeStorage(StorageContract):
    """T-P1-14-01
    The shared storage suite passes for `FakeStorage`.
    """

    impl = "fake"
    adapter_name = "knowledge.server_path"

    @pytest.fixture
    def subject(self, fakes: Fakes) -> StorageBackend:
        backend: StorageBackend = fakes[self.adapter_name]
        return backend


class TestFakeStorageForS3(TestFakeStorage):  # inherits the markers
    """T-P1-14-01
    The same fake, as registered for the S3 adapter.
    """

    adapter_name = "knowledge.s3"
