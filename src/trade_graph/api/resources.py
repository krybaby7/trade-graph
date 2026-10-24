"""Bounded cached host observations, timestamped separately from database facts."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from time import monotonic

from trade_graph.domain.clock import utc_iso


def observation(runtime) -> dict:
    lock = getattr(runtime, "_dashboard_resource_lock", None)
    if lock is None:
        lock = runtime._dashboard_resource_lock = threading.Lock()
    with lock:
        old = getattr(runtime, "_dashboard_resources", None)
        if old and monotonic() - old[0] < 30:
            return old[1]
        values, warnings = {}, []
        try:
            disk = shutil.disk_usage(runtime.database.path.parent)
            values["disk"] = {"free_bytes": disk.free, "total_bytes": disk.total}
            if disk.free < 1024**3:
                warnings.append("Less than 1 GiB free in database storage.")
        except OSError:
            values["disk"] = None
            warnings.append("Storage capacity observation unavailable.")
        for name in ("memory.current", "memory.max", "memory.events", "pids.current", "pids.max", "pids.events"):
            try:
                text = (Path("/sys/fs/cgroup") / name).read_text()[:4096].strip()
                if name.endswith("events"):
                    values[name] = {key: int(number) for key, number in (line.split() for line in text.splitlines())}
                else:
                    values[name] = int(text) if text.isdigit() else text
            except (OSError, ValueError):
                values[name] = None
        memory = values.get("memory.events") or {}
        if memory.get("oom", 0) or memory.get("oom_kill", 0):
            warnings.append("Container has recorded memory exhaustion events; counters are cumulative.")
        if (values.get("pids.events") or {}).get("max", 0):
            warnings.append("Container has recorded task-capacity events; counters are cumulative.")
        for prefix in ("memory", "pids"):
            current, maximum = values.get(prefix + ".current"), values.get(prefix + ".max")
            if type(current) is int and type(maximum) is int and current * 10 >= maximum * 9:
                warnings.append(f"Container {prefix} consumption is at least 90% of its limit.")
        result = {"observed_at": utc_iso(runtime.clock.now()), "source": "local filesystem and container cgroup",
                  "values": values, "warnings": warnings, "cache_seconds": 30,
                  "limits": "A point-in-time observation does not prove worst-case resource capacity."}
        runtime._dashboard_resources = monotonic(), result
        return result
