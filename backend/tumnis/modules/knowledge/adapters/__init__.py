"""knowledge adapters: one file per outside dependency, each with a fake.

The storage backends (P1-14) share one port and one fake: `FakeStorage` stands in for a
server path, S3 and SFTP (P3-14) alike. The virus scanner (P1-16) is `knowledge.clamav`,
the vision model `knowledge.vision`, whose real factory imports `adapters/vision.py` only
when the extract worker builds it (import-linter `api-never-calls-out`). Docling
(`DoclingExtractor`, `FakeDocling`) is not an outside call: the pipeline builds it in the
extract worker, and the extraction set (T-P1-16-06) runs it for real. The Obsidian vault
readers (P3-12), `knowledge.obsidian_folder` and `knowledge.obsidian_git`, share `FakeVault`.
"""

import importlib
from typing import Any

from tumnis.core.adapters.registry import register_adapter
from tumnis.modules.knowledge.adapters.clamav import ClamAV
from tumnis.modules.knowledge.adapters.fake import FakeClamAV, FakeStorage, FakeVision
from tumnis.modules.knowledge.adapters.obsidian.fake import FakeVault
from tumnis.modules.knowledge.adapters.obsidian.folder import FolderReader
from tumnis.modules.knowledge.adapters.obsidian.git import GitReader
from tumnis.modules.knowledge.adapters.obsidian.port import VaultReader
from tumnis.modules.knowledge.adapters.port import Scanner, Vision
from tumnis.modules.knowledge.adapters.s3 import S3Storage
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
register_adapter("knowledge.obsidian_folder", port=VaultReader, real=FolderReader, fake=FakeVault)
register_adapter("knowledge.obsidian_git", port=VaultReader, real=GitReader, fake=FakeVault)
