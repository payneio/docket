"""Ask the kernel who is calling.

Over a unix socket the operating system already knows the caller's uid, and it
cannot be lied about. Nothing in HTTP carries that, and no interoperability
standard specifies it — A2A and the rest authenticate with bearer credentials
because they must cross a network. On one machine we can do better, and the
difference matters: a token says who the caller claims to be, ``SO_PEERCRED``
says who it is.

Uvicorn builds each request's scope with ``self.client``, which for an AF_UNIX
connection is ``None``. Setting it in ``connection_made`` therefore costs no
information and reaches the application as ``scope["client"] == ("uid", 40001)``.
"""

import socket
import struct

from uvicorn.protocols.http.auto import AutoHTTPProtocol

_UCRED = struct.Struct("3i")  # pid, uid, gid


class PeerCredProtocol(AutoHTTPProtocol):  # type: ignore[misc,valid-type]
    """Uvicorn HTTP protocol that reports the peer's uid as ``scope['client']``."""

    def connection_made(self, transport):  # noqa: ANN001, ANN201
        super().connection_made(transport)
        sock = transport.get_extra_info("socket")
        if sock is None or sock.family != socket.AF_UNIX:
            return
        try:
            raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _UCRED.size)
        except OSError:
            return
        _pid, uid, _gid = _UCRED.unpack(raw)
        self.client = ("uid", uid)
