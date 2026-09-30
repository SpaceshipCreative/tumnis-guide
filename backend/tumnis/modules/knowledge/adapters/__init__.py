"""knowledge adapters: one file per outside dependency, each with a fake.

The storage backends (P1-14) share one port and one fake: `FakeStorage` stands in for a
server path, S3 and SFTP (P3-14) alike. The virus scanner (P1-16) is `knowledge.clamav`; the vision
model and Docling register with impl-2, each with its contract suite.
"""

from tumnis.core.adapters.registry import register_adapter
from tumnis.modules.knowledge.adapters.clamav import ClamAV
from tumnis.modules.knowledge.adapters.fake import FakeClamAV, FakeStorage
from tumnis.modules.knowledge.adapters.port import Scanner
from tumnis.modules.knowledge.adapters.s3 import S3Storage
from tumnis.modules.knowledge.adapters.server_path import ServerPathStorage
from tumnis.modules.knowledge.adapters.sftp import SftpStorage
from tumnis.modules.knowledge.storage import StorageBackend

register_adapter(
    "knowledge.server_path", port=StorageBackend, real=ServerPathStorage, fake=FakeStorage
)
register_adapter("knowledge.s3", port=StorageBackend, real=S3Storage, fake=FakeStorage)
register_adapter("knowledge.sftp", port=StorageBackend, real=SftpStorage, fake=FakeStorage)
register_adapter("knowledge.clamav", port=Scanner, real=ClamAV, fake=FakeClamAV)
