# Test cases for .semgrep/tumnis.yml (run: semgrep --test .semgrep). Not imported by
# anything; .semgrepignore keeps the repository scan off this folder.
# ruff: noqa
import hmac

import httpx
import sqlalchemy as sa
from sqlalchemy import text


def secrets_compared(given_token, stored_token, key, row, expected_hmac, tokens_used):
    # ruleid: tumnis-secret-eq
    if given_token == stored_token:
        pass
    # ruleid: tumnis-secret-eq
    if row.secret_hash != key:
        pass
    # ruleid: tumnis-secret-eq
    if key == expected_hmac:
        pass
    # ok: tumnis-secret-eq
    if hmac.compare_digest(given_token, stored_token):
        pass
    # ok: tumnis-secret-eq
    if given_token == None:
        pass
    # ok: tumnis-secret-eq
    if row.token_type == "bearer":
        pass
    # ok: tumnis-secret-eq
    if tokens_used == 0:
        pass
    # ok: tumnis-secret-eq
    if len(given_token) != 43:
        pass


async def raw_clients(policy):
    # ruleid: tumnis-raw-httpx
    client = httpx.AsyncClient(timeout=5)
    # ruleid: tumnis-raw-httpx
    sync_client = httpx.Client()
    from tumnis.core.net import guarded_client

    # ok: tumnis-raw-httpx
    guarded = guarded_client(policy, timeout=5.0)
    return client, sync_client, guarded


# ruleid: tumnis-no-requests
import requests

# ruleid: tumnis-no-requests
from urllib.request import urlopen

# ruleid: tumnis-no-requests
import urllib.request

# ok: tumnis-no-requests
import urllib.parse


async def queries(session, conn, name, cursor):
    # ruleid: tumnis-sql-fstring
    await session.execute(text(f"SELECT * FROM tasks WHERE title = '{name}'"))
    # ruleid: tumnis-sql-fstring
    await session.execute(sa.text(f"DELETE FROM {name}"))
    # ruleid: tumnis-sql-fstring
    cursor.execute(f"SELECT {name}")
    # ok: tumnis-sql-fstring
    await session.execute(text("SELECT * FROM tasks WHERE title = :name"), {"name": name})
    # ok: tumnis-sql-fstring
    cursor.execute("SELECT %s", (name,))


def logging_calls(logger, log, request, prompt_text):
    # ruleid: tumnis-log-body
    logger.info("request", body=request.body)
    # ruleid: tumnis-log-body
    log.warning("sent", prompt=prompt_text)
    # ok: tumnis-log-body
    logger.info("request", body_bytes=len(request.body), path=request.url.path)
    # ok: tumnis-log-body
    request.send(body=b"")
