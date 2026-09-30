"""coolify adapters: one file per outside dependency, each with a fake.

`coolify.status` is Coolify's REST API, read-only (`coolify_status.py`, replayed by
`fake.py`)."""

from tumnis.core.adapters.registry import register_adapter
from tumnis.modules.coolify.adapters.coolify_status import CoolifyStatusApi
from tumnis.modules.coolify.adapters.fake import FakeCoolifyStatus
from tumnis.modules.coolify.adapters.port import CoolifyStatus

register_adapter(
    "coolify.status", port=CoolifyStatus, real=CoolifyStatusApi, fake=FakeCoolifyStatus
)
