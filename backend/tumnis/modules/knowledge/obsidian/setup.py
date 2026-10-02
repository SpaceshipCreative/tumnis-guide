"""Setting up an Obsidian vault connection (P3-12, FR-15.10, Scott decision 82).

Git host keys are confirmed on first connect, then pinned strictly:

- `probe_git_host` asks the remote's SSH server for its host key (no login; through the
  SSRF guard, as the SFTP location probe does) and answers it as a known_hosts line with
  its SHA256 fingerprint, for ObsidianSetup to show. Nothing is stored until the person
  confirms that fingerprint.
- `pinned_known_hosts` takes the confirmed key, or a known_hosts line the person pasted
  instead, and returns the one line `GitReader` checks against: the host spelled the way
  OpenSSH looks it up under `HostKeyAlias` (`host`, or `[host]:port` off port 22). A
  hashed (`|1|`) or marked (`@cert-authority`, `@revoked`) line, a line for another host or
  a key that does not parse is refused (`InvalidHostKey`).
- After that, a server showing another key fails the sync with `host_key_changed`
  (`GitReader`): nothing is ever accepted automatically.
"""

from dataclasses import dataclass, replace

import asyncssh

from tumnis.core.adapters.base import AdapterRejected
from tumnis.core.net import NetPolicy, Resolver, system_resolver
from tumnis.modules.knowledge.adapters.obsidian.git import (
    SSH_PORT,
    SSH_PORTS,
    GitReader,
    InvalidRemote,
    parse_remote,
)
from tumnis.modules.knowledge.adapters.sftp import fingerprint, probe_host_key

__all__ = ["HostKey", "InvalidHostKey", "host_alias", "pinned_known_hosts", "probe_git_host"]


class InvalidHostKey(AdapterRejected):
    code = "invalid_host_key"

    def __init__(self, reason: str) -> None:
        super().__init__(GitReader.name, "host_key", reason)


@dataclass(frozen=True)
class HostKey:
    known_hosts: str  # "<host> <type> <base64>", the line GitReader pins
    sha256: str  # "SHA256:…", as `ssh-keygen -lf` shows it


def host_alias(remote_url: str, net: NetPolicy) -> str:
    """The name the remote's host key is pinned under (`HostKeyAlias`); InvalidRemote for a
    remote with no SSH host."""
    remote = parse_remote(remote_url, net)
    if remote.kind != "ssh" or remote.host is None:
        raise InvalidRemote("a file:// remote has no host key")
    return remote.host if remote.port == SSH_PORT else f"[{remote.host}]:{remote.port}"


async def probe_git_host(
    remote_url: str, *, net: NetPolicy, resolver: Resolver = system_resolver
) -> HostKey:
    """The host key the remote's SSH server shows now, to confirm (nothing is stored)."""
    remote = parse_remote(remote_url, net)
    if remote.kind != "ssh" or remote.host is None:
        raise InvalidRemote("a file:// remote has no host key")
    policy = replace(net, ports=net.ports | SSH_PORTS)
    probed = await probe_host_key(remote.host, remote.port, net_policy=policy, resolver=resolver)
    return HostKey(
        known_hosts=f"{host_alias(remote_url, net)} {probed.openssh}", sha256=probed.sha256
    )


def pinned_known_hosts(remote_url: str, line: str, *, net: NetPolicy) -> HostKey:
    """The known_hosts line to pin for the remote, from a confirmed probe answer or a
    line the person pasted (`<hosts> <type> <base64> [comment]`)."""
    alias = host_alias(remote_url, net)
    fields = line.strip().split()
    if len(fields) < 3 or fields[0].startswith(("@", "|")):  # noqa: PLR2004  # hosts, type, key
        raise InvalidHostKey("paste one plain known_hosts line: host, key type and key")
    if alias not in fields[0].split(","):
        raise InvalidHostKey(f"the line is not for {alias}")
    openssh = f"{fields[1]} {fields[2]}"
    try:
        asyncssh.import_public_key(openssh)
    except (ValueError, asyncssh.KeyImportError) as exc:
        raise InvalidHostKey("the host key does not parse") from exc
    return HostKey(known_hosts=f"{alias} {openssh}", sha256=fingerprint(openssh))
