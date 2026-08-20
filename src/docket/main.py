"""The service.

Two listeners, one app. TCP on loopback for wild-life's API and the readiness
probe; a unix socket for anything on this machine that should be identified
rather than believed. The socket is the interesting one: over it the kernel
reports the caller's uid and no credential is presented at all.

The socket is bound here rather than by uvicorn so its group and mode are set
*before* it is listening. Doing it afterwards leaves a window in which the
permissions are whatever the umask happened to be.
"""

import asyncio
import grp
import os
import socket

import uvicorn
from fastapi import FastAPI

from docket.config import settings
from docket.peercred import PeerCredProtocol
from docket.routers import entries, health, identities

app = FastAPI(
    title="Docket",
    description="A register of what is owed, by whom, to whom, and how it is going.",
    version="0.1.0",
)
app.include_router(health.router)
app.include_router(entries.router)
app.include_router(identities.router)


def _bind_socket(path: str, group: str) -> socket.socket:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        os.unlink(path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(path)
    try:
        os.chown(path, -1, grp.getgrnam(group).gr_gid)
    except (KeyError, PermissionError):
        pass  # no such group, or not ours to give away: fall back to owner-only
    # Connecting to a unix socket needs *write* on the inode, so 0660 is what
    # lets the fleet in and keeps everyone else out.
    os.chmod(path, 0o660)
    sock.listen(128)
    return sock


async def _serve() -> None:
    servers = [uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port))]
    if settings.uds:
        sock = _bind_socket(settings.uds, settings.uds_group)
        servers.append(
            uvicorn.Server(uvicorn.Config(app, fd=sock.fileno(), http=PeerCredProtocol))
        )
    await asyncio.gather(*(s.serve() for s in servers))


def run() -> None:
    asyncio.run(_serve())
