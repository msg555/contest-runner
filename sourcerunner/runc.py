import copy
import json
import tempfile
import os
import subprocess

from typing import Any, Iterable, Mapping


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

    def create_config(self) -> Any:
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
        with tempfile.TemporaryDirectory() as tmp_dir:
            with open(
                os.path.join(tmp_dir, "config.json"), "w", encoding="utf-8"
            ) as fconfig:
                json.dump(self.create_config(), fconfig)

            result = subprocess.run(
                ["runc", "--root", self.runc_root, "run", "--bundle", tmp_dir, "somectr"],
            )
            print(result)



