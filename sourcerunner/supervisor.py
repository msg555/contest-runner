import argparse
import copy
import ctypes
import functools
import json
import os
import select
import shlex
import subprocess
import sys
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


def wait_ctr(
    ctr_id: str, pid: int, timeout_cpu: None | float, timeout_wall: None | float
):
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
                wall_time_rem = timeout_wall - (time.perf_counter() - time_start)
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


def child_setup(fd_remap, fd_opens):
    # Close all file descriptors we don't need.
    fd_lo = 0
    fd_max = os.sysconf("SC_OPEN_MAX")
    for fd in sorted(fd_remap.values()):
        if fd > fd_lo:
            os.closerange(fd_lo, fd)
        fd_lo = fd + 1
    os.closerange(fd_lo, fd_max)

    # Remap free chains
    done_remaps = set()
    fd_used = set(fd_remap.values())
    for fd_to, fd_from in fd_remap.items():
        if fd_to == fd_from:
            done_remaps.add(fd_to)
            continue
        if fd_to in fd_used:
            continue

        while True:
            os.dup2(fd_from, fd_to)
            os.close(fd_from)
            done_remaps.add(fd_to)

            fd_to = fd_from
            fd_from = fd_remap.get(fd_to, -1)
            if fd_from == -1:
                break

    # Relax cycles
    for fd_to, fd_from in fd_remap.items():
        if fd_to in done_remaps:
            continue

        fd_from_new = os.dup(fd_from)
        os.close(fd_from)
        fd_form = fd_from_new

        fd_to_orig = fd_to
        while True:
            os.dup2(fd_from, fd_to)
            os.close(fd_from)
            done_remaps.add(fd_to)

            if fd_from == fd_to_orig:
                break
            fd_to = fd_from
            fd_from = fd_remap[fd_to]

    for fd_to, fd_open in sorted(fd_opens.items()):
        fd = fd_open()
        if fd != fd_to:
            os.dup2(fd, fd_to)
            os.close(fd)
        else:
            os.set_inheritable(fd, True)


def run(
    runc_root: str,
    ctr_dir: str,
    ctr_id: str,
    timeout_cpu: None | float,
    timeout_wall: None | float,
    fd_remap: dict[int, int],
    fd_opens: dict[int, Any],
):
    runc_base_cmd = ("runc", f"--root={runc_root}")

    pid_file_path = os.path.join(ctr_dir, "ctr.pid")

    env = dict(os.environ)
    env["LISTEN_FDS"] = "0"
    preserve_fds = (
        max(
            (
                2,
                max(fd_remap, default=0),
                max(fd_opens, default=0),
            )
        )
        - 2
    )
    subprocess.run(
        [
            *runc_base_cmd,
            "create",
            "--no-new-keyring",
            "--pid-file",
            pid_file_path,
            f"--preserve-fds={preserve_fds}",
            "--bundle",
            ctr_dir,
            ctr_id,
        ],
        env=env,
        preexec_fn=functools.partial(child_setup, fd_remap, fd_opens),
        close_fds=False,
        check=True,
    )
    for fd in fd_remap.values():
        os.close(fd)

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
    parser.add_argument(
        "--fd-read-file",
        action="append",
        default=[],
    )
    parser.add_argument(
        "--fd-write-file",
        action="append",
        default=[],
    )
    parser.add_argument(
        "--fd-remap",
        action="append",
        default=[],
    )
    args = parser.parse_args()

    # Set as process tree subreaper
    ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)

    fd_opens = {}
    fd_remap = {}
    for fd_spec in args.fd_read_file:
        parts = fd_spec.split(":", 1)
        fd_no = int(parts[0])
        fd_opens[fd_no] = functools.partial(os.open, parts[1], os.O_RDONLY)
    for fd_spec in args.fd_write_file:
        parts = fd_spec.split(":", 1)
        fd_no = int(parts[0])
        fd_opens[fd_no] = functools.partial(os.open, parts[1], os.O_WRONLY | os.O_CREAT)
    for fd_spec in args.fd_remap:
        parts = fd_spec.split(":", 1)
        fd_to = int(parts[0])
        fd_from = int(parts[1])
        fd_remap[fd_to] = fd_from

    sys.stderr.write(json.dumps(fd_remap, indent=2))

    return run(
        args.runc_root,
        args.dir,
        args.ctr_id,
        args.timeout_cpu,
        args.timeout_wall,
        fd_remap,
        fd_opens,
    )


if __name__ == "__main__":
    try:
        result = main()
        result["supervisor_success"] = True
    except Exception as exc:
        result = {
            "supervisor_success": False,
            "error": str(exc),
        }
    print(json.dumps(result, indent=2))
