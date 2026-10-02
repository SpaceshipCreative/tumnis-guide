"""knowledge adapters: one file per outside dependency, each with a fake.

The storage backends (P1-14) share one port and one fake: `FakeStorage` stands in for a
server path, S3 and SFTP (P3-14) alike. The virus scanner (P1-16) is `knowledge.clamav`,
the vision model `knowledge.vision`, whose real factory imports `adapters/vision.py` only
when the extract worker builds it (import-linter `api-never-calls-out`). Docling
(`DoclingExtractor`, `FakeDocling`) is not an outside call: the pipeline builds it in the
extract worker, and the extraction set (T-P1-16-06) runs it for real. An S3 linked source
(P3-13) has its read-only connector, `knowledge.s3_source`, and the key capability check,
`knowledge.key_capabilities`.
"""

import importlib
from typing import Any

from tumnis.core.adapters.registry import register_adapter
from tumnis.modules.knowledge.adapters.clamav import ClamAV
from tumnis.modules.knowledge.adapters.fake import FakeClamAV, FakeStorage, FakeVision
from tumnis.modules.knowledge.adapters.port import (
    KeyCapabilityCheck,
    S3SourceReader,
    Scanner,
    Vision,
)
from tumnis.modules.knowledge.adapters.s3 import S3Storage
from tumnis.modules.knowledge.adapters.s3_source.capability import KeyCapabilityChecker
from tumnis.modules.knowledge.adapters.s3_source.connector import S3SourceConnector
from tumnis.modules.knowledge.adapters.s3_source.fake import FakeKeyCapabilities, FakeS3Source
from tumnis.modules.knowledge.adapters.server_path import ServerPathStorage
from tumnis.modules.knowledge.adapters.sftp import SftpStorage
from tumnis.modules.knowledge.storage import StorageBackend

VISION_MODULE = "tumnis.modules.knowledge.adapters.vision"


def build_vision(**deps: Any) -> Vision:
    """VllmVision(base_url, model, clock=..., net_policy=...), imported on first use."""
    vision: Vision = importlib.import_module(VISION_MODULE).VllmVision(**deps)
    return vision


register_adapter(
    "knowledge.server_path", port=StorageBackend, real=ServerPathStorage, fake=FakeStorage
)
register_adapter("knowledge.s3", port=StorageBackend, real=S3Storage, fake=FakeStorage)
register_adapter("knowledge.sftp", port=StorageBackend, real=SftpStorage, fake=FakeStorage)
register_adapter("knowledge.clamav", port=Scanner, real=ClamAV, fake=FakeClamAV)
register_adapter("knowledge.vision", port=Vision, real=build_vision, fake=FakeVision)
register_adapter(
    "knowledge.s3_source", port=S3SourceReader, real=S3SourceConnector, fake=FakeS3Source
)
register_adapter(
    "knowledge.key_capabilities",
    port=KeyCapabilityCheck,
    real=KeyCapabilityChecker,
    fake=FakeKeyCapabilities,
)
