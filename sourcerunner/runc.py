import copy
import json
import tempfile
import os
import subprocess
import uuid
import shlex

from typing import Any, Iterable, Mapping, Optional, Dict


CGROUP_ROOT = "/sys/fs/cgroup"
CGROUP_MEMORY_PEAK = "memory.peak"
CGROUP_MEMORY_EVENTS = "memory.events"

BASE_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "runc-base-config.json")

def get_base_config():
    if BASE_CONFIG is not None:
        return BASE_CONFIG


class RuncRunner:
    BASE_CONFIG = None
    DEFAULT_ENV = {
			"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
			"TERM": "xterm"
    }

    @classmethod
    def _base_config(cls) -> Any:
        if cls.BASE_CONFIG is None:
            with open(BASE_CONFIG_PATH, "r", encoding="utf-8") as fbase_config:
                cls.BASE_CONFIG = json.load(fbase_config)
        return cls.BASE_CONFIG

    def create_config(self, ctr_id: str) -> Any:
        config = copy.deepcopy(self._base_config())
        config["process"]["user"] = {
            "uid": self.uid,
            "gid": self.gid
        }
        config["process"]["args"] = self.args
        config["process"]["env"] = [
            f"{key}={val}" for key, val in self.env.items()
        ]
        config["process"]["cwd"] = self.cwd

        # TODO: set rlimits
        
        config["root"] = {
            "path": self.fsroot,
            "readonly": False,
        }

        #cgroup_path = os.path.join(CGROUP_ROOT, ctr_id)
        #config["hooks"]["createRuntime"].append(
        #    {
        #        "path": "/usr/bin/bash",
        #        "args": ["bash", "-c", f"echo 1 > {shlex.quote(cgroup_path)}/memory.oom.group"]
        #    }
        #)
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
    ) -> None:
        self.fsroot = fsroot
        self.args = list(args)
        self.env = self.DEFAULT_ENV | (env or {})
        self.uid = uid
        self.gid = gid
        self.cwd = cwd
        self.runc_root = runc_root

    def run(self) -> None:
        runc_base_cmd = (
            "runc", "--rootless=false", f"--root={self.runc_root}"
        )
        ctr_id = str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as tmp_dir:
            with open(
                os.path.join(tmp_dir, "config.json"), "w", encoding="utf-8"
            ) as fconfig:
                json.dump(self.create_config(ctr_id), fconfig)

            print("Container", ctr_id)

            # TODO: Specify cpus to pin to one CPU?
            # linux config info: https://github.com/opencontainers/runtime-spec/blob/main/config-linux.md
            # Can specify seccomp profile, probably should
            try:
                result = subprocess.run(
                    [*runc_base_cmd, "run", "--keep", "--bundle", tmp_dir, ctr_id],
                )

                print(result)

                mem_peak = self._cgroup_read_scalar(ctr_id, "memory.peak", None)
                mem_events = self._cgroup_read_dict(ctr_id, "memory.events", {})
                print("mem_peak", mem_peak)
                print("mem_events", mem_events)
                
                try:
                    with open(os.path.join(CGROUP_ROOT, ctr_id, "memory.peak"), "r") as fmem:
                        mem_peak = fmem.read()
                except FileNotFoundError:
                    print("could not find memory.peak")
                else:
                    print("peak memory", mem_peak)
            finally:
                if False:
                    result = subprocess.run(
                        [*runc_base_cmd, "delete", ctr_id],
                    )

    def _cgroup_read_scalar(self, ctr_id: str, key: str, default: Optional[int] = None) -> Optional[int]:
        try:
            with open(os.path.join(CGROUP_ROOT, ctr_id, key), "r") as fcg_scalar:
                return int(fcg_scalar.read().strip())
        except (IOError, ValueError):
            LOGGER.warning("Failed to read %s", key, exc_info=True)
            return default

    def _cgroup_read_dict(
        self,
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
