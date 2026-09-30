"""knowledge adapters: one file per outside dependency, each with a fake.

The storage backends (P1-14) share one port and one fake: `FakeStorage` stands in for a
server path and for S3 alike. The virus scanner (P1-16) is `knowledge.clamav`, the vision
model `knowledge.vision` (`VllmVision`). Docling (`DoclingExtractor`, `FakeDocling`) is not
an outside call: the pipeline builds it in the extract worker, and the extraction set
(T-P1-16-06) runs it for real.
"""

from tumnis.core.adapters.registry import register_adapter
from tumnis.modules.knowledge.adapters.clamav import ClamAV
from tumnis.modules.knowledge.adapters.fake import FakeClamAV, FakeStorage, FakeVision
from tumnis.modules.knowledge.adapters.port import Scanner, Vision
from tumnis.modules.knowledge.adapters.s3 import S3Storage
from tumnis.modules.knowledge.adapters.server_path import ServerPathStorage
from tumnis.modules.knowledge.adapters.vision import VllmVision
from tumnis.modules.knowledge.storage import StorageBackend

register_adapter(
    "knowledge.server_path", port=StorageBackend, real=ServerPathStorage, fake=FakeStorage
)
register_adapter("knowledge.s3", port=StorageBackend, real=S3Storage, fake=FakeStorage)
register_adapter("knowledge.clamav", port=Scanner, real=ClamAV, fake=FakeClamAV)
register_adapter("knowledge.vision", port=Vision, real=VllmVision, fake=FakeVision)
