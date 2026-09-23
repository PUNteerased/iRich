"""Host machine telemetry for the iRich Hardware dashboard page."""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

_BOOT_MONO = time.monotonic()
_CPU_NAME_CACHE: str | None = None


def _cpu_name() -> str:
    global _CPU_NAME_CACHE
    if _CPU_NAME_CACHE:
        return _CPU_NAME_CACHE
    name = platform.processor() or ""
    if platform.system() == "Windows":
        try:
            out = subprocess.check_output(
                ["wmic", "cpu", "get", "Name"],
                text=True,
                timeout=2,
                stderr=subprocess.DEVNULL,
            )
            lines = [ln.strip() for ln in out.splitlines() if ln.strip() and ln.strip().lower() != "name"]
            if lines:
                name = lines[0]
        except Exception:  # noqa: BLE001
            pass
    _CPU_NAME_CACHE = name or "CPU"
    return _CPU_NAME_CACHE


def _nvidia_gpus() -> list[dict[str, Any]]:
    """Optional NVIDIA stats via nvidia-smi (no extra Python deps)."""
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,utilization.memory,memory.total,memory.used,temperature.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=2,
            stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001
        return []

    gpus: list[dict[str, Any]] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        try:
            gpus.append(
                {
                    "name": parts[0],
                    "util_pct": float(parts[1]),
                    "mem_util_pct": float(parts[2]),
                    "vram_total_mb": float(parts[3]),
                    "vram_used_mb": float(parts[4]),
                    "temp_c": float(parts[5]),
                    "power_w": float(parts[6]) if parts[6] and parts[6] != "[N/A]" else None,
                }
            )
        except ValueError:
            continue
    return gpus


def _disk_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        import psutil
    except ImportError:
        usage = shutil.disk_usage(os.path.splitdrive(os.getcwd())[0] + os.sep if os.name == "nt" else "/")
        return [
            {
                "mount": "/",
                "total_gb": round(usage.total / (1024**3), 1),
                "used_gb": round(usage.used / (1024**3), 1),
                "free_gb": round(usage.free / (1024**3), 1),
                "pct": round(100.0 * usage.used / usage.total, 1) if usage.total else 0.0,
            }
        ]

    for part in psutil.disk_partitions(all=False):
        if part.device in seen:
            continue
        seen.add(part.device)
        try:
            u = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        rows.append(
            {
                "device": part.device,
                "mount": part.mountpoint,
                "fstype": part.fstype,
                "total_gb": round(u.total / (1024**3), 1),
                "used_gb": round(u.used / (1024**3), 1),
                "free_gb": round(u.free / (1024**3), 1),
                "pct": float(u.percent),
            }
        )
    return rows


def _irich_processes() -> list[dict[str, Any]]:
    try:
        import psutil
    except ImportError:
        return []

    keys = ("main.py", "telemetry_api.py", "ngrok", "terminal64")
    found: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "cpu_percent", "memory_info"]):
        try:
            info = proc.info
            cmd = " ".join(info.get("cmdline") or [])
            name = (info.get("name") or "").lower()
            blob = f"{name} {cmd}".lower()
            if not any(k in blob for k in keys):
                continue
            mem = info.get("memory_info")
            found.append(
                {
                    "pid": info.get("pid"),
                    "name": info.get("name"),
                    "cmdline": (cmd[:120] + "…") if len(cmd) > 120 else cmd,
                    "cpu_pct": float(info.get("cpu_percent") or 0.0),
                    "rss_mb": round((mem.rss / (1024**2)) if mem else 0.0, 1),
                }
            )
        except (psutil.Error, OSError):
            continue
    found.sort(key=lambda r: r.get("cpu_pct") or 0.0, reverse=True)
    return found[:12]


def build_hardware() -> dict[str, Any]:
    """Collect host metrics. Soft-fails fields when psutil / nvidia missing."""
    ts = datetime.now(timezone.utc).isoformat()
    hostname = socket.gethostname()
    try:
        import psutil
    except ImportError:
        return {
            "available": False,
            "error": "psutil_not_installed",
            "ts": ts,
            "hostname": hostname,
            "hint": "pip install psutil",
        }

    # First call often returns 0; non-blocking interval.
    cpu_pct = float(psutil.cpu_percent(interval=None))
    per_cpu = [float(x) for x in psutil.cpu_percent(interval=None, percpu=True)]
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    boot = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc).isoformat()
    net = psutil.net_io_counters()
    try:
        batt = psutil.sensors_battery()
    except Exception:  # noqa: BLE001
        batt = None
    temps: dict[str, float] = {}
    try:
        sensor_temps = psutil.sensors_temperatures(fahrenheit=False) or {}
        for label, entries in sensor_temps.items():
            if entries:
                temps[label] = float(entries[0].current)
    except Exception:  # noqa: BLE001
        pass

    gpus = _nvidia_gpus()
    disks = _disk_rows()

    return {
        "available": True,
        "ts": ts,
        "hostname": hostname,
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "cpu": {
            "name": _cpu_name(),
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(logical=True),
            "freq_mhz": float(psutil.cpu_freq().current) if psutil.cpu_freq() else None,
            "pct": cpu_pct,
            "per_cpu": per_cpu,
            "load_avg": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
        },
        "memory": {
            "total_gb": round(vm.total / (1024**3), 2),
            "used_gb": round(vm.used / (1024**3), 2),
            "available_gb": round(vm.available / (1024**3), 2),
            "pct": float(vm.percent),
            "swap_total_gb": round(swap.total / (1024**3), 2),
            "swap_used_gb": round(swap.used / (1024**3), 2),
            "swap_pct": float(swap.percent),
        },
        "disks": disks,
        "network": {
            "bytes_sent": int(net.bytes_sent),
            "bytes_recv": int(net.bytes_recv),
            "packets_sent": int(net.packets_sent),
            "packets_recv": int(net.packets_recv),
        },
        "gpu": gpus,
        "temps_c": temps,
        "battery": (
            {
                "pct": float(batt.percent),
                "plugged": bool(batt.power_plugged),
                "secs_left": (
                    int(getattr(batt, "secsleft", getattr(batt, "secs_left", -1)))
                    if int(getattr(batt, "secsleft", getattr(batt, "secs_left", -1))) > 0
                    else None
                ),
            }
            if batt
            else None
        ),
        "boot_time": boot,
        "uptime_sec": int(time.time() - psutil.boot_time()),
        "api_uptime_sec": int(time.monotonic() - _BOOT_MONO),
        "processes": _irich_processes(),
    }
