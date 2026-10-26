from __future__ import annotations

import asyncio
import contextlib
import copy
import ctypes
import json
import os
import re
import socket
import sys
import tempfile
import uuid
from typing import Any, Iterable, Literal, Mapping

from pydantic import BaseModel

CGROUP_ROOT = "/sys/fs/cgroup"
CGROUP_MEMORY_PEAK = "memory.peak"
CGROUP_MEMORY_EVENTS = "memory.events"

BASE_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "runc-base-config.json")


IGNORE_PARTITION_NAME = re.compile(r"^(loop|ram|zram|sr|nbd|fd)\d+$")


class FDContainer:
    def __init__(self, fd: int, *, set_inheritable: bool = True) -> None:
        self.fd = fd
        self.closed = False
        if set_inheritable:
            os.set_inheritable(fd, True)

    @classmethod
    def pipe(cls) -> tuple["FDContainer", "FDContainer"]:
        p = os.pipe()
        return (cls(p[0]), cls(p[1]))

    @classmethod
    def socketpair(cls) -> tuple["FDContainer", "FDContainer"]:
        p = socket.socketpair()
        return (cls(p[0].fileno()), cls(p[1].fileno()))

    @classmethod
    def dup(cls, fd: int | FDContainer) -> "FDContainer":
        if isinstance(fd, FDContainer):
            fd = fd.fd
        return cls(os.dup(fd))

    def __enter__(self):
        return self.fd

    def __exit__(self, exc_type, exc_value, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        if not self.closed:
            os.close(self.fd)
            self.closed = True


@contextlib.asynccontextmanager
async def wrap_sync_cm(cm):
    loop = asyncio.get_running_loop()
    try:
        yield await loop.run_in_executor(None, cm.__enter__)
    finally:
        await loop.run_in_executor(None, cm.__exit__, *sys.exc_info())


class RuncResult(BaseModel):
    exit_type: Literal["normal", "killed", "oom", "signal", "unknown", "supervisor"]
    error: str | None = None
    kill_reason: str | None = None
    time_cpu: float
    time_sys: float
    time_wall: float
    mem_peak: int
    signal: int | None = None
    exit_status: int | None = None


class RuncExecutionError(Exception):
    pass


class RuncRunner:
    IS_SUBREAPER = False
    BASE_CONFIG = None
    DISK_DEVICES = None
    DEFAULT_ENV = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "TERM": "xterm",
    }

    @classmethod
    def _set_subreaper(cls):
        if not cls.IS_SUBREAPER:
            ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)
            cls.IS_SUBREAPER = True

    @classmethod
    def _base_config(cls) -> Any:
        if cls.BASE_CONFIG is None:
            with open(BASE_CONFIG_PATH, "r", encoding="utf-8") as fbase_config:
                cls.BASE_CONFIG = json.load(fbase_config)
        return cls.BASE_CONFIG

    @classmethod
    def _disk_devices(cls):
        """
        Enumerate all physical disk devices on the system.
        """
        if cls.DISK_DEVICES is not None:
            return cls.DISK_DEVICES

        with open("/proc/partitions", "r", encoding="utf-8") as fpart:
            lines = fpart.readlines()

        result = {}
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) != 4:
                continue

            try:
                dev_major = int(parts[0])
                dev_minor = int(parts[1])
            except ValueError:
                continue

            part_name = parts[3]
            if IGNORE_PARTITION_NAME.match(part_name):
                continue

            if not os.path.exists(
                os.path.join("/sys/class/block", part_name, "partition")
            ):
                result[part_name] = {"major": dev_major, "minor": dev_minor}

        cls.DISK_DEVICES = result
        return result

    def create_config(self) -> Any:
        config = copy.deepcopy(self._base_config())
        config["process"]["user"] = {"uid": self.uid, "gid": self.gid}
        config["process"]["args"] = self.args
        config["process"]["env"] = [f"{key}={val}" for key, val in self.env.items()]
        config["process"]["cwd"] = self.cwd

        config["linux"]["resources"]["memory"] = {
            "limit": self.mem_limit_bytes,
            "reservation": self.mem_limit_bytes,
            "swap": self.mem_limit_bytes,
            "kernel": -1,
            "kernelTCP": -1,
            "swappiness": 0,
            "disableOOMKiller": False,
        }

        # Fix to max 1 CPU-second/wall-second
        config["linux"]["resources"]["cpu"] = {
            "quota": 100000,
            "period": 100000,
            "cpus": "1",
        }
        config["linux"]["resources"]["pids"] = {
            "limit": 10000,  # self.pid_limit,
        }

        io_config = {}
        disk_devices = list(self._disk_devices().values())
        if self.io_max_read_bps is not None:
            io_config["throttleReadBpsDevice"] = [
                {**disk_device, "rate": self.io_max_read_bps}
                for disk_device in disk_devices
            ]
        if self.io_max_read_iops is not None:
            io_config["throttleReadIOPSDevice"] = [
                {**disk_device, "rate": self.io_max_read_iops}
                for disk_device in disk_devices
            ]
        if self.io_max_write_bps is not None:
            io_config["throttleWriteBpsDevice"] = [
                {**disk_device, "rate": self.io_max_write_bps}
                for disk_device in disk_devices
            ]
        if self.io_max_write_iops is not None:
            io_config["throttleWriteIOPSDevice"] = [
                {**disk_device, "rate": self.io_max_write_iops}
                for disk_device in disk_devices
            ]
        if io_config:
            config["linux"]["resources"]["blockIO"] = io_config

        config["root"] = {
            "path": self.fsroot,
            "readonly": False,
        }
        return config

    def __init__(
        self,
        fsroot: str,
        *,
        args: Iterable[str],
        env: Mapping[str, str] | None = None,
        uid: int = 1000,
        gid: int = 1000,
        cwd: str = "/task",
        runc_root: str = "/tmp",
        mem_limit_bytes: int = 2**29,  # 9,
        pid_limit: int = 64,
        timeout_cpu: float | None = 10.0,
        timeout_wall: float | None = 10.0,
        io_max_write_bps: int | None = 32 * 2**20,
        io_max_write_iops: int | None = 1000,
        io_max_read_bps: int | None = None,
        io_max_read_iops: int | None = 1000,
    ) -> None:
        self.fsroot = fsroot
        self.args = list(args)
        self.env = self.DEFAULT_ENV | dict(env or {})
        self.uid = uid
        self.gid = gid
        self.cwd = cwd
        self.runc_root = runc_root
        self.mem_limit_bytes = mem_limit_bytes
        self.pid_limit = pid_limit
        self.timeout_cpu = timeout_cpu
        self.timeout_wall = timeout_wall
        self.io_max_read_bps = io_max_read_bps
        self.io_max_read_iops = io_max_read_iops
        self.io_max_write_bps = io_max_write_bps
        self.io_max_write_iops = io_max_write_iops

    async def run(
        self,
        *,
        fd_map: dict[int, int | FDContainer | tuple[str, bool]] | None = None,
        close_fds: Iterable[FDContainer | int] = (),
    ) -> RuncResult:
        if fd_map is None:
            fd_map = {}
        fd_map.setdefault(0, 0)
        fd_map.setdefault(1, 1)
        fd_map.setdefault(2, 2)

        ctr_id = str(uuid.uuid4())
        async with contextlib.AsyncExitStack() as exit_stack:
            tmp_dir = await exit_stack.enter_async_context(
                wrap_sync_cm(tempfile.TemporaryDirectory())
            )

            def _sync_write_config():
                with open(
                    os.path.join(tmp_dir, "config.json"), "w", encoding="utf-8"
                ) as fconfig:
                    json.dump(self.create_config(), fconfig)

            await asyncio.get_running_loop().run_in_executor(None, _sync_write_config)

            supervisor_args = [
                sys.executable,
                "-m",
                "sourcerunner.supervisor",
                f"--runc_root={self.runc_root}",
                tmp_dir,
                ctr_id,
            ]

            nfds = [-1, -1, -1]
            for fd, fd_desc in fd_map.items():
                if isinstance(fd_desc, FDContainer):
                    fd_desc = fd_desc.fd
                match fd_desc:
                    case int():
                        if 1 <= fd_desc <= 2:
                            # Protect stdout/stderr
                            if nfds[fd_desc] == -1:
                                nfds[fd_desc] = exit_stack.enter_context(
                                    FDContainer.dup(fd_desc)
                                )
                            fd_desc = nfds[fd_desc]
                        supervisor_args.append(f"--fd-remap={fd}:{fd_desc}")
                    case (str() as path, bool() as write):
                        if write:
                            supervisor_args.append(f"--fd-write-file={fd}:{path}")
                        else:
                            supervisor_args.append(f"--fd-write-file={fd}:{path}")

            if self.timeout_cpu is not None:
                supervisor_args.append(f"--timeout-cpu={self.timeout_cpu}")
            if self.timeout_wall is not None:
                supervisor_args.append(f"--timeout-wall={self.timeout_wall}")

            def child_setup():
                fd_want = {1, 2}
                for nfd in nfds:
                    if nfd != -1:
                        fd_want.add(nfd)
                for fd_desc in fd_map.values():
                    if isinstance(fd_desc, int):
                        fd_want.add(fd_desc)
                    elif isinstance(fd_desc, FDContainer):
                        fd_want.add(fd_desc.fd)

                fd_last = 0
                for fd in sorted(list(fd_want)):
                    if fd_last != fd:
                        os.closerange(fd_last, fd)
                    fd_last = fd + 1

                fd_max = os.sysconf("SC_OPEN_MAX")
                os.closerange(fd_last, fd_max)

            proc = await asyncio.create_subprocess_exec(
                *supervisor_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=child_setup,
                close_fds=False,
            )

            for fd_to_close in close_fds:
                if isinstance(fd_to_close, FDContainer):
                    fd_to_close.close()
                else:
                    os.close(fd_to_close)

            stdout, stderr = await proc.communicate()
            sys.stderr.buffer.write(stderr + b"\n")
            if proc.returncode != 0:
                raise RuncExecutionError("supervisor processor failed unexpectedly")

            try:
                result = RuncResult.model_validate_json(stdout)
            except ValueError as exc:
                raise RuncExecutionError(
                    "got unexpected data format back from supervisor"
                ) from exc

            if result.exit_type == "supervisor":
                raise RuncExecutionError(
                    f"supervisor failed unexpectedly with error: {result.error}"
                )

            return result
