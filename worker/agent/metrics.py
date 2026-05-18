"""
System metrics collection for Apple Silicon.

Collects CPU, memory, and GPU utilization for the heartbeat payload.
GPU metrics use ioreg (no sudo required on macOS).
All collection is non-blocking and falls back gracefully on error.
"""
from __future__ import annotations

import asyncio
import logging
import re
import socket
from dataclasses import dataclass

import psutil

logger = logging.getLogger(__name__)


@dataclass
class SystemMetrics:
    cpu_percent: float | None
    memory_used_gb: float | None
    memory_total_gb: float | None
    memory_percent: float | None
    gpu_percent: float | None
    gpu_memory_used_gb: float | None = None

    def to_dict(self) -> dict:
        return {
            "cpu_percent": self.cpu_percent,
            "memory_used_gb": self.memory_used_gb,
            "memory_total_gb": self.memory_total_gb,
            "memory_percent": self.memory_percent,
            "gpu_percent": self.gpu_percent,
            "gpu_memory_used_gb": self.gpu_memory_used_gb,
        }


async def collect_metrics() -> SystemMetrics:
    """Collect system metrics asynchronously, falling back on any error."""
    # CPU and memory are fast and don't block significantly
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()

    gpu_percent = await _get_apple_gpu_percent()

    return SystemMetrics(
        cpu_percent=round(cpu, 1),
        memory_used_gb=round(mem.used / (1024 ** 3), 2),
        memory_total_gb=round(mem.total / (1024 ** 3), 2),
        memory_percent=round(mem.percent, 1),
        gpu_percent=gpu_percent,
    )


async def _get_apple_gpu_percent() -> float | None:
    """
    Read GPU utilization from Apple Silicon via ioreg.
    Does not require sudo. Returns None on any error.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ioreg", "-r", "-d", "1", "-w", "0", "-n", "IOGPU",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        output = stdout.decode(errors="replace")

        for line in output.splitlines():
            if "Device Utilization" in line:
                match = re.search(r"(\d+(?:\.\d+)?)", line)
                if match:
                    return float(match.group(1))
    except Exception:
        pass
    return None


def get_hardware_info() -> dict:
    """
    Collect static hardware information for worker registration.
    Returns dict compatible with WorkerRegisterRequest.
    """
    import platform
    import subprocess

    info: dict = {
        "hostname": socket.gethostname(),
        "ip_address": _get_local_ip(),
        "cpu_cores": psutil.cpu_count(logical=False) or psutil.cpu_count(),
        "memory_total_gb": round(psutil.virtual_memory().total / (1024 ** 3), 2),
    }

    # CPU model (Apple Silicon chip name)
    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=2,
        )
        cpu_model = result.stdout.strip()
        if not cpu_model:
            # On Apple Silicon, use a different sysctl
            result = subprocess.run(
                ["sysctl", "-n", "hw.model"],
                capture_output=True, text=True, timeout=2,
            )
            cpu_model = result.stdout.strip()
        info["cpu_model"] = cpu_model or platform.processor()
    except Exception:
        info["cpu_model"] = platform.processor()

    # GPU model
    try:
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True, text=True, timeout=5,
        )
        import json
        data = json.loads(result.stdout)
        displays = data.get("SPDisplaysDataType", [])
        if displays:
            gpu_model = displays[0].get("sppci_model", "")
            if gpu_model:
                info["gpu_model"] = gpu_model
    except Exception:
        pass

    # MAC address (for backward compat)
    try:
        import uuid as _uuid
        mac = _uuid.getnode()
        mac_str = ":".join(
            f"{(mac >> (8 * i)) & 0xFF:02X}" for i in reversed(range(6))
        )
        info["mac_address"] = mac_str
    except Exception:
        info["mac_address"] = "00:00:00:00:00:00"

    return info


def _get_local_ip() -> str:
    """Return the primary LAN IP (no packet sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
