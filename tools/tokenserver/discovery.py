"""Best-effort DNS-SD advertisement for the local VibePulse service.

The tokenserver deliberately remains runnable with the Python standard
library only.  When ``zeroconf`` is installed we advertise the already-bound
HTTP listener; otherwise startup continues with the configured-address path.
No account data, credentials, repository names, or quota values enter mDNS.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass


SERVICE_TYPE = "_vibepulse._tcp.local."
PROTOCOL_VERSION = "1"


def _safe_dns_label(value: str) -> str:
    label = "".join(
        char.lower() if char.isascii() and char.isalnum() else "-"
        for char in value
    ).strip("-")
    while "--" in label:
        label = label.replace("--", "-")
    return (label or "host")[:48]


def _local_ipv4_addresses() -> tuple[bytes, ...]:
    """Return distinct routable local IPv4 addresses in packed form."""
    found: set[str] = set()
    try:
        rows = socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM
        )
    except OSError:
        rows = []
    for row in rows:
        try:
            address = ipaddress.ip_address(row[4][0])
        except (IndexError, ValueError):
            continue
        if not isinstance(address, ipaddress.IPv4Address):
            continue
        if address.is_loopback or address.is_link_local or address.is_unspecified:
            continue
        found.add(str(address))
    return tuple(socket.inet_aton(value) for value in sorted(found))


@dataclass
class DiscoveryAdvertiser:
    """Own one optional zeroconf registration and close it deterministically."""

    logger: logging.Logger
    status: str = "off"
    reason: str | None = None
    _zeroconf: object | None = None
    _service_info: object | None = None

    def start(self, port: int) -> bool:
        if not 1 <= port <= 65535:
            self.status = "error"
            self.reason = "invalid-port"
            return False
        try:
            from zeroconf import ServiceInfo, Zeroconf
        except ImportError:
            self.status = "unavailable"
            self.reason = "dependency-missing"
            self.logger.info(
                "lokal VibePulse-upptäckt ej annonserad (zeroconf saknas)"
            )
            return False

        addresses = _local_ipv4_addresses()
        if not addresses:
            self.status = "unavailable"
            self.reason = "no-lan-address"
            self.logger.info(
                "lokal VibePulse-upptäckt väntar (ingen LAN-adress hittad)"
            )
            return False

        host_label = _safe_dns_label(socket.gethostname())
        service_name = f"VibePulse-{host_label}.{SERVICE_TYPE}"
        server_name = f"vibepulse-{host_label}.local."
        info = ServiceInfo(
            SERVICE_TYPE,
            service_name,
            addresses=list(addresses),
            port=port,
            properties={b"v": PROTOCOL_VERSION.encode("ascii")},
            server=server_name,
        )
        zc = None
        try:
            zc = Zeroconf()
            zc.register_service(info, allow_name_change=True)
        except Exception as exc:  # optional transport must never kill service
            if zc is not None:
                try:
                    zc.close()
                except Exception:  # noqa: S110 - already on the error path; the warning below carries the cause
                    pass
            self.status = "error"
            self.reason = type(exc).__name__
            self.logger.warning(
                "lokal VibePulse-upptäckt kunde inte annonseras (%s)",
                type(exc).__name__,
            )
            return False

        self._zeroconf = zc
        self._service_info = info
        self.status = "ready"
        self.reason = None
        self.logger.info("lokal VibePulse-upptäckt annonserad via mDNS")
        return True

    def stop(self) -> None:
        zc, info = self._zeroconf, self._service_info
        self._zeroconf = None
        self._service_info = None
        if zc is None:
            return
        try:
            if info is not None:
                zc.unregister_service(info)
        except Exception:
            self.logger.warning("kunde inte avregistrera VibePulse mDNS rent")
        finally:
            try:
                zc.close()
            except Exception:
                self.logger.warning("kunde inte stänga VibePulse mDNS rent")
