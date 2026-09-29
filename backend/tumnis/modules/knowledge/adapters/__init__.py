"""knowledge adapters: one file per outside dependency, each with a fake.

The storage backends (P1-14) share one port and one fake: `FakeStorage` stands in for a
server path and for S3 alike.
"""

from tumnis.core.adapters.registry import register_adapter
from tumnis.modules.knowledge.adapters.fake import FakeStorage
from tumnis.modules.knowledge.adapters.s3 import S3Storage
from tumnis.modules.knowledge.adapters.server_path import ServerPathStorage
from tumnis.modules.knowledge.storage import StorageBackend

register_adapter(
    "knowledge.server_path", port=StorageBackend, real=ServerPathStorage, fake=FakeStorage
)
register_adapter("knowledge.s3", port=StorageBackend, real=S3Storage, fake=FakeStorage)
