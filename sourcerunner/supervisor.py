import argparse
import copy
import ctypes
import json
import os
import select
import shlex
import subprocess
import tempfile
import time
import uuid
from typing import Any, Dict, Iterable, Mapping, Optional

CGROUP_ROOT = "/sys/fs/cgroup"
CGROUP_MEMORY_PEAK = "memory.peak"
CGROUP_MEMORY_EVENTS = "memory.events"


def cgroup_kill(ctr_id: str) -> None:
    try:
        with open(os.path.join(CGROUP_ROOT, ctr_id, "cgroup.kill"), "w") as fkill:
            fkill.write("1")
    except (IOError, ValueError):
        LOGGER.warning("Failed to kill cgroup %s", ctr_id, exc_info=True)

def cgroup_read_scalar(
    ctr_id: str, key: str, default: Optional[int] = None
) -> Optional[int]:
    try:
        with open(os.path.join(CGROUP_ROOT, ctr_id, key), "r") as fcg_scalar:
            return int(fcg_scalar.read().strip())
    except (IOError, ValueError):
        LOGGER.warning("Failed to read %s", key, exc_info=True)
        return default


def cgroup_read_dict(
    ctr_id: str,
    key: str,
    default: Optional[Dict[str, int]] = None,
) -> Dict[str, int]:
    try:
        result = {}
        with open(os.path.join(CGROUP_ROOT, ctr_id, key), "r") as fcg_dict:
            for line in fcg_dict:
                parts = line.strip().split(" ", 1)
                if len(parts) == 2:
                    result[parts[0]] = int(parts[1])
        return result
    except (IOError, ValueError):
        LOGGER.warning("Failed to read %s", key, exc_info=True)
        return default


def wait_ctr(ctr_id: str, pid: int, timeout_cpu: None | float, timeout_wall: None | float):
    time_start = time.perf_counter()
    ep = select.epoll()
    pid_fd = os.pidfd_open(pid)
    ep.register(pid_fd, select.EPOLLIN)

    kill_reason = None
    exit_time_wall = None
    try:
        while True:
            poll_timeout = None

            if timeout_cpu is not None:
                cpu = cgroup_read_dict(ctr_id, "cpu.stat", None)
                cpu_time_rem = timeout_cpu - cpu["usage_usec"] * 1e-6
                if cpu_time_rem < 0:
                    kill_reason = "TIMEOUT_CPU"
                    break

                poll_timeout = cpu_time_rem * 1.1 + 1e-3

            if exit_time_wall is not None:
                break

            if timeout_wall is not None:
                wall_time_rem = timeout_wall - (
                    time.perf_counter() - time_start
                )
                if wall_time_rem < 0:
                    kill_reason = "TIMEOUT_WALL"
                    break

                poll_timeout = (
                    min(wall_time_rem, poll_timeout)
                    if poll_timeout is not None
                    else wall_time_rem
                )

            if ep.poll(poll_timeout):
                exit_time_wall = time.perf_counter() - time_start
    finally:
        if kill_reason:
            cgroup_kill(ctr_id)
        st = os.waitid(os.P_PIDFD, pid_fd, os.WEXITED)
        os.close(pid_fd)
        ep.close()

    if exit_time_wall is None:
        exit_time_wall = time.perf_counter() - time_start

    mem_peak = cgroup_read_scalar(ctr_id, "memory.peak", None)
    mem_events = cgroup_read_dict(ctr_id, "memory.events", {})

    exit_status = None
    signal = None
    if kill_reason:
        exit_type = "killed"
    elif st.si_code == os.CLD_EXITED:
        exit_type = "normal"
        exit_status = st.si_status
    elif st.si_code == os.CLD_KILLED:
        if mem_events["oom"]:
            exit_type = "oom"
        else:
            exit_type = "signal"
            signal = st.si_status
    else:
        exit_type = "Unknown"

    cpu = cgroup_read_dict(ctr_id, "cpu.stat", None)
    return {
        "exit_type": exit_type,
        "kill_reason": kill_reason,
        "time_cpu": cpu["usage_usec"] * 1e-6,
        "time_sys": cpu["system_usec"] * 1e-6,
        "time_wall": exit_time_wall,
        "mem_peak": mem_peak,
        "signal": signal,
        "exit_status": exit_status,
    }



def run(runc_root: str, ctr_dir: str, ctr_id: str, timeout_cpu: None | float, timeout_wall: None | float):
    runc_base_cmd = ("runc", f"--root={runc_root}", "--debug")

    pid_file_path = os.path.join(ctr_dir, "ctr.pid")
    subprocess.run(
        [
            *runc_base_cmd,
            "create",
            "--no-new-keyring",
            "--pid-file",
            pid_file_path,
            "--bundle",
            ctr_dir,
            ctr_id,
        ],
        check=True,
    )
    with open(pid_file_path, "r", encoding="utf-8") as fpid:
        pid = int(fpid.read().strip())
    result = subprocess.run(
        [*runc_base_cmd, "start", ctr_id],
        check=True,
    )

    try:
        return wait_ctr(ctr_id, pid, timeout_cpu, timeout_wall)
    finally:
        result = subprocess.run(
            [*runc_base_cmd, "delete", "-f", ctr_id],
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runc_root",
        default="/tmp",
    )
    parser.add_argument("dir")
    parser.add_argument("ctr_id")
    parser.add_argument(
        "--timeout-cpu",
        type=float,
        default=None,
    )
    parser.add_argument(
        "--timeout-wall",
        type=float,
        default=None,
    )
    args = parser.parse_args()

    # Set as process tree subreaper
    ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)

    try:
        result = run(args.runc_root, args.dir, args.ctr_id, args.timeout_cpu, args.timeout_wall)
        result["supervisor_success"] = True
    except Exception as exc:
        result = {
            "supervisor_success": False,
            "error": str(exc),
        }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
