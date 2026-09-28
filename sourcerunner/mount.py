import os
import subprocess
from tempfile import TemporaryDirectory
from typing import Optional

# Reference for fuse-overlayfs. Probably should do some uid/gid mapping
# https://github.com/containers/fuse-overlayfs


class TaskWorkspace:
    def __init__(
        self,
        language: str,
        *,
        task_dir_name: str = "task",
        runners_path: str = "/runners",
        workspace_path: str = "/mnt/sourcerunner",
        temp_dir: Optional[str] = None,
        rootless: bool = False,
    ) -> None:
        self.temp_dir = TemporaryDirectory(dir=workspace_path, delete=False)
        self.task_dir_name = task_dir_name
        self.upper_dir = os.path.join(self.temp_dir.name, "upper")
        self.work_dir = os.path.join(self.temp_dir.name, "work")
        self.mount_dir = os.path.join(self.temp_dir.name, "mount")
        self.lower_dirs = [
            os.path.join(runners_path, "lower"),
            os.path.join(runners_path, language),
        ]
        self.mounted = False
        self.rootless = rootless
        os.chmod(self.temp_dir.name, 0o711)
        os.mkdir(self.upper_dir)
        os.mkdir(self.work_dir)
        os.mkdir(self.mount_dir)
        print("Mkdir", self.mount_dir)

    def __enter__(self) -> "TaskWorkspace":
        if not self.mounted:
            self.mount()
        return self

    def __exit__(self, exc_type, exc_value, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        pass
        #if self.mounted:
        #    self.unmount()
        #self.temp_dir.cleanup()

    def mount(self) -> None:
        if self.rootless:
            cmd = ["fuse-overlayfs"]
        else:
            cmd = ["mount", "-toverlay", "overlay"]
        cmd.append(f"-olowerdir={':'.join(self.lower_dirs)}")
        cmd.append(f"-oupperdir={self.upper_dir}")
        cmd.append(f"-oworkdir={self.work_dir}")
        cmd.append(self.mount_dir)
        subprocess.run(cmd, check=True)
        self.mounted = True

    def unmount(self) -> None:
        if not self.mounted:
            raise RuntimeError("Workspace already unmounted")
        subprocess.run(["umount", self.mount_dir], check=True)
        self.mounted = False
