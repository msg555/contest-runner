#!/usr/bin/env python3
"""
Temporary check (not part of sourcerunner): verifies that task containers stay
on the worker container's pinned CPU (run_worker.sh --cpuset-cpus).

Run inside the worker container, from the repo root:
    python3 cpu_pin_test.py

Starts one task container (python3 runner) that reports its CPU affinity,
cgroup cpuset, CPU quota, and the CPU it ran on after a short busy loop, then
compares these with the worker's own affinity. Exits non-zero on mismatch.
"""

import asyncio
import os
import sys

from sourcerunner.mount import TaskWorkspace
from sourcerunner.runc import FDContainer, RuncRunner

PROBE = r"""
import os, time

def read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError as exc:
        return "<" + type(exc).__name__ + ">"

end = time.process_time() + 0.5
while time.process_time() < end:
    pass
with open("/proc/self/stat") as f:
    last_cpu = f.read().rsplit(")", 1)[1].split()[36]

print("affinity", ",".join(map(str, sorted(os.sched_getaffinity(0)))))
print("cpuset", read("/sys/fs/cgroup/cpuset.cpus.effective"))
print("cpu.max", read("/sys/fs/cgroup/cpu.max"))
print("last_cpu", last_cpu)
"""


def parse_cpu_list(text: str) -> set[int] | None:
    """Parses a cpuset list like '0-3,5'; None if unreadable."""
    if not text or text.startswith("<"):
        return None
    cpus: set[int] = set()
    for part in text.replace(",", " ").split():
        lo, _, hi = part.partition("-")
        cpus.update(range(int(lo), int(hi or lo) + 1))
    return cpus


def read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError as exc:
        return f"<{type(exc).__name__}>"


async def run_probe() -> tuple[dict[str, str], str]:
    read_end, write_end = FDContainer.pipe()
    async with TaskWorkspace("python3") as workspace:
        runner = RuncRunner(
            workspace.mount_dir,
            cwd="/",
            args=["python3", "-c", PROBE],
            timeout_cpu=5,
            timeout_wall=10,
        )
        result = await runner.run(
            fd_map={1: write_end}, close_fds=[write_end], inherit_stdio=False
        )
    with os.fdopen(read_end.fd, "r") as f:
        read_end.closed = True
        output = f.read()
    values = {}
    for line in output.splitlines():
        key, _, value = line.partition(" ")
        values[key] = value
    return values, f"{result.exit_type} exit_status={result.exit_status}"


def main() -> int:
    worker_affinity = os.sched_getaffinity(0)
    print(f"worker affinity:        {sorted(worker_affinity)}")
    print(
        f"worker cpuset:          {read_file('/sys/fs/cgroup/cpuset.cpus.effective')}"
    )

    values, outcome = asyncio.run(run_probe())
    print(f"task run:               {outcome}")
    for key in ("affinity", "cpuset", "cpu.max", "last_cpu"):
        print(f"task {key + ':':<19}{values.get(key, '<missing>')}")

    failures = []
    if len(worker_affinity) != 1:
        failures.append(
            "worker is not pinned to a single CPU (check --cpuset-cpus in "
            "run_worker.sh)"
        )
    task_affinity = parse_cpu_list(values.get("affinity", ""))
    if task_affinity != worker_affinity:
        failures.append(f"task affinity {task_affinity} != worker {worker_affinity}")
    task_cpuset = parse_cpu_list(values.get("cpuset", ""))
    if task_cpuset is not None and task_cpuset != worker_affinity:
        failures.append(f"task cpuset {task_cpuset} != worker {worker_affinity}")
    last_cpu = values.get("last_cpu", "")
    if not last_cpu.isdigit() or int(last_cpu) not in worker_affinity:
        failures.append(f"task ran on CPU {last_cpu!r}, outside {worker_affinity}")
    if not values.get("cpu.max", "").startswith("100000 100000"):
        failures.append(f"unexpected task cpu.max {values.get('cpu.max')!r}")

    for failure in failures:
        print(f"FAIL: {failure}")
    if not failures:
        print("PASS: task container is confined to the worker's CPU")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
