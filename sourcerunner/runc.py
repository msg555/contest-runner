import re
import os
import copy
import ctypes
import json
import os
import select
import shlex
import sys
import subprocess
import tempfile
import time
import uuid
from typing import Any, Dict, Iterable, Mapping, Optional

CGROUP_ROOT = "/sys/fs/cgroup"
CGROUP_MEMORY_PEAK = "memory.peak"
CGROUP_MEMORY_EVENTS = "memory.events"

BASE_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "runc-base-config.json")


IGNORE_PARTITION_NAME = re.compile(r"^(loop|ram|zram|sr|nbd|fd)\d+$")



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
            
            if not os.path.exists(os.path.join("/sys/class/block", part_name, "partition")):
                result[part_name] = {"major": dev_major, "minor": dev_minor}

        cls.DISK_DEVICES = result
        return result

    def create_config(self, ctr_id: str) -> Any:
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
            "limit": 10000, #self.pid_limit,
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
        env: Mapping[str, str] = None,
        uid: int = 1000,
        gid: int = 1000,
        cwd: str = "/task",
        runc_root: str = "/tmp",
        mem_limit_bytes: int = 2**29,  # 9,
        pid_limit: int = 64,
        timeout_cpu: float | None = 10.0,
        timeout_wall: float | None = 10.0,
        io_max_write_bps: int | None = 32 * 2 ** 20,
        io_max_write_iops: int | None =  1000,
        io_max_read_bps: int | None = None,
        io_max_read_iops: int | None =  1000,
    ) -> None:
        self.fsroot = fsroot
        self.args = list(args)
        self.env = self.DEFAULT_ENV | (env or {})
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

    def run(self) -> None:
        runc_base_cmd = ("runc", f"--root={self.runc_root}")
        ctr_id = str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as tmp_dir:
            with open(
                os.path.join(tmp_dir, "config.json"), "w", encoding="utf-8"
            ) as fconfig:
                json.dump(self.create_config(ctr_id), fconfig)

            supervisor_args = []
            if self.timeout_cpu is not None:
                supervisor_args.append(f"--timeout-cpu={self.timeout_cpu}")
            if self.timeout_wall is not None:
                supervisor_args.append(f"--timeout-wall={self.timeout_wall}")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sourcerunner.supervisor",
                    f"--runc_root={self.runc_root}",
                    "--fd-read-file=0:/dev/null",
                    f"--fd-write-file=1:{os.path.join(tmp_dir, "stdout")}",
                    f"--fd-write-file=2:{os.path.join(tmp_dir, "stderr")}",
                    tmp_dir,
                    ctr_id,
                    *supervisor_args,
                ],
                stdout=subprocess.PIPE,
                check=True,
            )

            result = json.loads(result.stdout)
            with open(os.path.join(tmp_dir, "stdout"), "r") as fstdout:
                print(fstdout.read())
            with open(os.path.join(tmp_dir, "stderr"), "r") as fstderr:
                print(fstderr.read())
            return result
