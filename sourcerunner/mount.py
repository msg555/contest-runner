import os
from typing import Optional
from tempfile import TemporaryDirectory
import subprocess

# Reference for fuse-overlayfs. Probably should do some uid/gid mapping
# https://github.com/containers/fuse-overlayfs

class TaskWorkspace:
    def __init__(
        self,
        language: str,
        *,
        task_dir_name: str = "task",
        runners_path: str = "/runners",
        workspace_path: str = "/runnerdata/workspace",
        temp_dir: Optional[str] = None,
    ) -> None:
        self.temp_dir = TemporaryDirectory(dir=workspace_path)
        self.task_dir_name = task_dir_name
        self.upper_dir = os.path.join(self.temp_dir.name, "upper")
        self.work_dir = os.path.join(self.temp_dir.name, "work")
        self.mount_dir = os.path.join(self.temp_dir.name, "mount")
        self.task_dir = os.path.join(self.upper_dir, task_dir_name)
        self.lower_dirs = [
            os.path.join(runners_path, "lower"),
            os.path.join(runners_path, language),
        ]
        self.mounted = False
        os.mkdir(self.upper_dir)
        os.mkdir(self.work_dir)
        os.mkdir(self.mount_dir)
        os.mkdir(self.task_dir)

    def __enter__(self) -> "TaskWorkspace":
        if not self.mounted:
            self.mount()
        return self

    def __exit__(self, exc_type, exc_value, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        if self.mounted:
            self.unmount()
        self.temp_dir.cleanup()

    def mount(self) -> None:
        cmd = ["mount", "-toverlay", "overlay"]
        cmd.append(f"-olowerdir={':'.join(self.lower_dirs)}")
        cmd.append(f"-oupperdir={self.upper_dir}")
        cmd.append(f"-oworkdir={self.work_dir}")
        cmd.append(self.mount_dir)
        self.mounted = True
        subprocess.run(cmd)

    def unmount(self) -> None:
        if not self.mounted:
            raise RuntimeError("Workspace already unmounted")
        subprocess.run(["umount", self.mount_dir])
        self.mounted = False

