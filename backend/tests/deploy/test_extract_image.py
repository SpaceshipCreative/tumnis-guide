"""Docling in the extract worker's image (Scott decision 97, P1-16, ADR-0007).

`worker-extract` runs its own image, `deploy/Dockerfile`'s `app-extract` stage: the slim
image plus the `docling` dependency group (CPU torch) and Docling's models, fetched at build
time so a running deploy reaches neither Hugging Face nor ModelScope. The api and the other
workers keep the slim image (the Dockerfile's last stage, the default target).

The first tests read the files. The last two need Docker and run in the `docling` CI job
(.github/workflows/docling.yml), which builds the images and starts the stack:

- `TUMNIS_EXTRACT_IMAGE` names a built `app-extract` image: it converts the extraction
  fixtures with networking off, as its own user, on a read-only root, under the
  4 GiB memory limit compose.yaml gives worker-extract.
- `TUMNIS_REAL_STACK_URL` is the api of a fresh compose.yaml stack with real adapters
  (deploy/compose.test-real.yaml): a PDF and a DOCX with a table are uploaded, scanned by
  clamd, extracted by Docling and found through `GET /v1/knowledge/search`.

Without the variable each skips; `TUMNIS_EXTRACT_TESTS=require` (set by that job) makes a
missing variable a failure instead, so the job cannot pass by skipping.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests._auth import CSRF_COOKIE, secret_from_uri, totp_code
from tests.meta.test_compose import load_compose
from tumnis.settings import KnowledgeSettings

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "deploy"
FIXTURES = REPO / "backend" / "fixtures" / "extraction"
DATA = Path(__file__).resolve().parent / "data"
SLIM_IMAGE = "ghcr.io/spaceshipcreative/tumnis:latest"
EXTRACT_IMAGE = "ghcr.io/spaceshipcreative/tumnis:latest-extract"
EXTRACT_STAGE = "app-extract"
MEM_LIMIT = "4g"  # compose.yaml's worker-extract mem_limit
GiB = 1 << 30


def _needs(variable: str) -> str:
    value = os.environ.get(variable)
    if value:
        return value
    if os.environ.get("TUMNIS_EXTRACT_TESTS") == "require":
        pytest.fail(f"{variable} is not set")
    pytest.skip(f"{variable} is not set (the docling CI job sets it)")


# --- the Dockerfile --------------------------------------------------------------------


def _instructions() -> list[str]:
    """Every Dockerfile instruction, comments dropped and continuations joined."""
    lines: list[str] = []
    pending = ""
    for raw in (DEPLOY / "Dockerfile").read_text().splitlines():
        line = raw.strip()
        if line.startswith("#") or (not pending and not line):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        lines.append(pending + line)
        pending = ""
    return lines


def _stages() -> list[tuple[str, str, list[str]]]:
    """(name, base, instructions) per stage, in order; an unnamed stage is named by index."""
    stages: list[tuple[str, str, list[str]]] = []
    for line in _instructions():
        match = re.match(r"FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?$", line, re.IGNORECASE)
        if match:
            stages.append((match.group(2) or str(len(stages)), match.group(1), []))
        elif stages:
            stages[-1][2].append(line)
    return stages


def _chain(name: str) -> list[str]:
    """The instructions of stage `name` and of every stage it is built FROM, base first."""
    by_name = {stage: (base, body) for stage, base, body in _stages()}
    chain: list[str] = []
    while name in by_name:
        base, body = by_name[name]
        chain[:0] = body
        name = base
    return chain


def _env(chain: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in chain:
        if line.upper().startswith("ENV "):
            for pair in re.findall(r"(\w+)=(\S+)", line):
                env[pair[0]] = pair[1]
    return env


@pytest.mark.req("FR-15.2", "ADR-0007")
@pytest.mark.wp("P1-16")
@pytest.mark.xfail(strict=True, reason="spec:P1-16")
def test_extract_stage_adds_docling_and_its_models_to_the_slim_image_only() -> None:
    """The Dockerfile has an `app-extract` stage that installs the `docling` group, fetches
    the models at build time and turns Hugging Face's network access off at run time, as
    the `tumnis` user. The last stage (the default target: the api, the other workers,
    migrate) installs no Docling."""
    stages = _stages()
    names = [name for name, _, _ in stages]
    assert EXTRACT_STAGE in names, names
    assert names[-1] != EXTRACT_STAGE, "the slim image stays the default (last) target"

    extract = _chain(EXTRACT_STAGE)
    slim = _chain(names[-1])
    syncs = [line for line in extract if "uv sync" in line]
    assert syncs, "app-extract installs the backend with uv"
    assert all("--group docling" in line for line in syncs[-2:]), syncs
    assert not [line for line in slim if "--group docling" in line or "docling-tools" in line]

    env = _env(extract)
    assert env.get("HF_HUB_OFFLINE") == "1", env
    assert env.get("DOCLING_ARTIFACTS_PATH", "").startswith("/"), env
    assert "docling-tools models download" in " ".join(extract)
    # The chunker's tokenizer (KnowledgeSettings.chunk_tokenizer) is fetched into the image.
    assert KnowledgeSettings().chunk_tokenizer in " ".join(extract)
    users = [line.split()[1] for line in extract if line.upper().startswith("USER ")]
    assert users[-1:] == ["tumnis"], users


@pytest.mark.req("FR-15.2", "ADR-0007")
@pytest.mark.wp("P1-16")
@pytest.mark.xfail(strict=True, reason="spec:P1-16")
def test_only_worker_extract_runs_the_extract_image() -> None:
    """compose.yaml runs worker-extract on the extract image (the release's tag with
    `-extract`), and every other app service on the slim one. Previews run fakes, so
    their worker-extract stays on the slim image the preview workflow builds."""
    services = load_compose("compose.yaml")["services"]
    assert services["worker-extract"]["image"] == EXTRACT_IMAGE
    for name in ("migrate", "api", "worker"):
        assert services[name]["image"] == SLIM_IMAGE, name
    preview = load_compose("compose.preview.yaml")["services"]
    assert preview["worker-extract"]["image"] == SLIM_IMAGE


# --- the built image -------------------------------------------------------------------

CONVERT = """
import json, os, pathlib
from tumnis.modules.knowledge.adapters.docling import DoclingExtractor
from tumnis.settings import KnowledgeSettings
x = DoclingExtractor(chunk_tokenizer=KnowledgeSettings().chunk_tokenizer)
out = {"uid": os.getuid(), "chunks": {}}
for name, kind in json.loads(os.environ["FILES"]).items():
    rows = x.chunk(x.convert(pathlib.Path("/data") / name, kind).doc_json)
    out["chunks"][name] = [[r.text, r.heading_path, r.page_from] for r in rows]
peak = pathlib.Path("/sys/fs/cgroup/memory.peak")
out["memory_peak"] = int(peak.read_text()) if peak.exists() else None
print("RESULT " + json.dumps(out))
"""
# file -> (kind, snippets one chunk must hold); from the fixtures' expected files.
IMAGE_FILES: dict[str, tuple[str, list[str]]] = {
    "rate-card-table.pdf": ("pdf", ["Senior designer", "160"]),  # a table (TableFormer)
    "scanned-1p.pdf": ("pdf", ["Delivery note", "Twelve chairs"]),  # OCR (RapidOCR)
    "receipt.png": ("image", ["Flat white", "Croissant"]),
    "courier-rates.docx": ("docx", ["Zephyrine Couriers", "45"]),
}


@pytest.mark.req("FR-15.2", "ADR-0007")
@pytest.mark.wp("P1-16")
@pytest.mark.integration
@pytest.mark.enable_socket
@pytest.mark.slow
@pytest.mark.extract_image
def test_extract_image_converts_offline_as_its_user(tmp_path: Path) -> None:
    """The built `app-extract` image converts a PDF with a table, a scanned PDF, a photo and
    a DOCX with a table with no network at all (models and tokenizer are in the image), as
    `tumnis` (uid 10001), on a read-only root with only /tmp and /scratch writable, under
    worker-extract's 4 GiB memory limit."""
    image = _needs("TUMNIS_EXTRACT_IMAGE")
    docker = shutil.which("docker")
    assert docker, "docker is not on PATH"
    data = tmp_path / "data"
    data.mkdir()
    for name in IMAGE_FILES:
        source = DATA / name if (DATA / name).exists() else FIXTURES / name
        shutil.copy(source, data / name)
    data.chmod(0o755)
    files = json.dumps({name: kind for name, (kind, _) in IMAGE_FILES.items()})
    command = [docker, "run", "--rm", "--network", "none", "--read-only"]
    command += ["--tmpfs", "/tmp", "--tmpfs", "/scratch", "--memory", MEM_LIMIT]  # noqa: S108
    command += ["-v", f"{data}:/data:ro", "-e", f"FILES={files}", image, "python", "-c", CONVERT]
    done = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)  # noqa: S603
    assert done.returncode == 0, done.stderr[-4000:]
    line = next(x for x in done.stdout.splitlines() if x.startswith("RESULT "))
    result: dict[str, Any] = json.loads(line.removeprefix("RESULT "))

    assert result["uid"] == 10001
    for name, (_, snippets) in IMAGE_FILES.items():
        texts = [text for text, _, _ in result["chunks"][name]]
        assert any(all(s in text for s in snippets) for text in texts), (name, texts)
    if result["memory_peak"] is not None:
        print(f"extract image memory peak: {result['memory_peak'] / GiB:.2f} GiB")
        assert result["memory_peak"] < 4 * GiB


# --- the production stack ---------------------------------------------------------------

UPLOADS: dict[str, tuple[str, str, str, list[str], int | None]] = {
    # file -> (MIME, search query, snippet the hit holds, heading path tail, page)
    "rate-card-table.pdf": ("application/pdf", "Senior designer", "160", ["Rates"], 2),
    "courier-rates.docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Zephyrine",
        "45",
        ["Courier rates", "Rates"],
        None,
    ),
}
DONE = {"ready", "failed", "quarantined"}
PASSWORD = "real-stack-password-1"  # a throwaway stack


class _Session:
    """An httpx client that keeps the session cookies itself: the api sets them `Secure`
    (`__Host-`), and the stack is plain HTTP on loopback, where a cookie jar keeps them back.
    Writes carry the CSRF token and an Idempotency-Key, like the browser."""

    def __init__(self, base_url: str) -> None:
        self.cookies: dict[str, str] = {}
        self.client = httpx.Client(base_url=base_url, timeout=60, headers={"Origin": base_url})

    def __call__(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers: dict[str, str] = dict(kwargs.pop("headers", {}))
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if method != "GET":
            headers["Idempotency-Key"] = f"test-{uuid.uuid4()}"
            if CSRF_COOKIE in self.cookies:
                headers["X-CSRF-Token"] = self.cookies[CSRF_COOKIE]
        response = self.client.request(method, path, headers=headers, **kwargs)
        for header in response.headers.get_list("set-cookie"):
            name, _, value = header.split(";", 1)[0].partition("=")
            self.cookies[name.strip()] = value
        self.client.cookies.clear()
        return response


def _ok(response: httpx.Response) -> Any:
    assert response.is_success, (response.request.url, response.status_code, response.text)
    return response.json() if response.content else None


@pytest.mark.req("FR-15.2", "FR-15.3", "ADR-0007")
@pytest.mark.wp("P1-16")
@pytest.mark.integration
@pytest.mark.enable_socket
@pytest.mark.slow
@pytest.mark.extract_image
def test_real_stack_extracts_uploads_and_finds_their_passages() -> None:
    """On compose.yaml with real adapters (clamd, Docling in worker-extract's image): set
    up the workspace, add a server-folder location and a project, upload a PDF with a table
    and a DOCX with a table. Each is scanned, extracted and chunked and ends `ready` (never
    `extraction_failed`), and `GET /v1/knowledge/search` finds its table passage, citing
    the document, its heading path and (for the PDF) the page."""
    base = _needs("TUMNIS_REAL_STACK_URL").rstrip("/")
    call = _Session(base)

    started = call(
        "POST",
        "/v1/setup",
        json={"email": "owner@example.test", "password": PASSWORD, "timezone": "UTC"},
    )
    assert started.status_code in {200, 201}, f"needs a fresh stack: {started.text}"
    body = started.json()
    code = totp_code(secret_from_uri(body["otpauth_uri"]), datetime.now(UTC))
    _ok(call("POST", "/v1/setup/totp", json={"setup_token": body["setup_token"], "code": code}))

    root = os.environ.get("TUMNIS_REAL_STACK_FILES", "/srv/tumnis-files")
    location = {"name": "Server files", "kind": "server_path", "root": root, "is_default": True}
    _ok(call("POST", "/v1/knowledge/locations", json=location))
    project = _ok(call("POST", "/v1/projects", json={"name": "Extraction check"}))

    documents: dict[str, str] = {}
    for name, (mime, *_rest) in UPLOADS.items():
        source = DATA / name if (DATA / name).exists() else FIXTURES / name
        accepted = _ok(
            call(
                "POST",
                "/v1/knowledge/documents",
                data={"project_id": project["id"]},
                files={"file": (name, source.read_bytes(), mime)},
            )
        )
        documents[name] = accepted["id"]

    deadline = time.monotonic() + 600
    final: dict[str, dict[str, Any]] = {}
    while len(final) < len(documents) and time.monotonic() < deadline:
        for name, document_id in documents.items():
            if name not in final:
                doc = _ok(call("GET", f"/v1/knowledge/documents/{document_id}"))
                if doc["status"] in DONE:
                    final[name] = doc
        time.sleep(3)
    for name in documents:
        doc = final.get(name)
        assert doc is not None, f"{name} still not extracted after 10 minutes"
        assert (doc["status"], doc.get("status_reason")) == ("ready", None), (name, doc)

    for name, (_, query, snippet, heading_tail, page) in UPLOADS.items():
        found = _ok(
            call("GET", "/v1/knowledge/search", params={"q": query, "project_id": project["id"]})
        )
        hits = [hit for hit in found["items"] if hit["document_id"] == documents[name]]
        print(f"search {query!r}: {json.dumps(hits)[:1500]}")
        assert any(
            query in hit["text"]
            and snippet in hit["text"]
            and hit["heading_path"][-len(heading_tail) :] == heading_tail
            and hit["page"] == page
            for hit in hits
        ), (name, found)
