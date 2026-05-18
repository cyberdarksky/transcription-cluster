from __future__ import annotations

import asyncio
import logging
import socket

from zeroconf import ServiceInfo, Zeroconf

from ..config import settings

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_transcription._tcp.local."


class MDNSAnnouncer:
    """
    Announces the coordinator as a mDNS service on the local network.
    Workers use Zeroconf service browsing to discover the coordinator
    without any manual IP configuration.

    Service type: _transcription._tcp.local.
    """

    def __init__(self) -> None:
        self._zeroconf: Zeroconf | None = None
        self._service_info: ServiceInfo | None = None
        self._running = False

    async def start(self) -> None:
        try:
            ip = self._get_local_ip()
            hostname = socket.gethostname()

            self._service_info = ServiceInfo(
                type_=SERVICE_TYPE,
                name=f"{settings.service_name}.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(ip)],
                port=settings.coordinator_port,
                properties={
                    b"version": settings.coordinator_version.encode(),
                    b"name": hostname.encode(),
                },
                server=f"{hostname}.local.",
            )

            self._zeroconf = Zeroconf()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, self._zeroconf.register_service, self._service_info
            )
            self._running = True
            logger.info(
                "mDNS service announced",
                extra={
                    "service_type": SERVICE_TYPE,
                    "ip": ip,
                    "port": settings.coordinator_port,
                    "hostname": hostname,
                },
            )
        except Exception:
            logger.exception("Failed to start mDNS announcer — worker auto-discovery disabled")

    async def stop(self) -> None:
        if self._zeroconf and self._service_info and self._running:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None, self._zeroconf.unregister_service, self._service_info
                )
                self._zeroconf.close()
                self._running = False
                logger.info("mDNS service withdrawn")
            except Exception:
                logger.exception("Error stopping mDNS announcer")

    @staticmethod
    def _get_local_ip() -> str:
        """
        Find the primary local network IP (the one used for LAN traffic).
        Uses a UDP trick: connect to an external address (no packet sent)
        and read the socket's local address.
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.168.1.1", 80))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            s.close()
