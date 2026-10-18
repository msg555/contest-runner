import asyncio
import contextlib
import os
import shutil
import uuid
from contextlib import asynccontextmanager


class TaskWorkspace:
    def __init__(
        self,
        language: str,
        *,
        runners_path: str = "/runners",
        workspace_path: str = "/mnt/sourcerunner",
        read_only: bool = True,
    ) -> None:
        self.workspace_id = str(uuid.uuid4())
        self.temp_dir = os.path.join(workspace_path, self.workspace_id)
        self.upper_dir = os.path.join(self.temp_dir, "upper")
        self.work_dir = os.path.join(self.temp_dir, "work")
        self.mount_dir = os.path.join(self.temp_dir, "mount")
        self.lower_dirs = [
            os.path.join(runners_path, "lower"),
            os.path.join(runners_path, language),
        ]
        self.read_only = read_only
        self.scratch_space: list[tuple[str, str, bool]] = []
        self.exit_stack: contextlib.AsyncExitStack | None = None

    async def add_scratch(
        self,
        dst_path: str,
        *,
        src_path: str | None = None,
        src_path_owner: tuple[int, int] | None = None,
        read_only: bool = False,
    ) -> str:
        def _make_dir():
            os.mkdir(src_path)
            if src_path_owner is not None:
                os.chown(src_path, src_path_owner[0], src_path_owner[1])

        if src_path is None:
            src_path = os.path.join(self.temp_dir, f"scratch_{len(self.scratch_space)}")
            await asyncio.get_running_loop().run_in_executor(None, _make_dir)
        self.scratch_space.append((src_path, dst_path, read_only))
        if self.exit_stack:
            await self.exit_stack.enter_async_context(
                self._mount_scratch(src_path, dst_path, read_only)
            )
        return src_path

    @asynccontextmanager
    async def _setup_dirs(self):
        def _sync_setup_dir():
            os.mkdir(self.temp_dir)
            os.mkdir(self.upper_dir)
            os.mkdir(self.work_dir)
            os.mkdir(self.mount_dir)

        def _sync_cleanup_dir():
            shutil.rmtree(self.temp_dir)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _sync_setup_dir)
        yield
        await loop.run_in_executor(None, _sync_cleanup_dir)

    @asynccontextmanager
    async def _mount_overlay(self):  # type: ignore
        cmd = ["mount", "-toverlay", "overlay"]
        if self.read_only:
            cmd.append(f"-olowerdir={':'.join([*self.lower_dirs, self.upper_dir])}")
        else:
            cmd.append(f"-olowerdir={':'.join(self.lower_dirs)}")
            cmd.append(f"-oupperdir={self.upper_dir}")
        cmd.append(f"-oworkdir={self.work_dir}")
        cmd.append(self.mount_dir)

        proc = await asyncio.create_subprocess_exec(*cmd)
        result = await proc.wait()
        if result:
            raise RuntimeError(f"mount exited with code {result}")

        yield

        proc = await asyncio.create_subprocess_exec("umount", self.mount_dir)
        result = await proc.wait()
        if result:
            raise RuntimeError(f"umount exited with code {result}")

    @asynccontextmanager
    async def _mount_scratch(
        self, src_path: str, dst_path: str, read_only: bool
    ):  # type: ignore
        if dst_path.startswith("/"):
            dst_path = dst_path[1:]
        dst_path = os.path.join(self.mount_dir, dst_path)
        print(src_path, dst_path, self.mount_dir)
        proc = await asyncio.create_subprocess_exec(
            "mount", "--bind", "--", src_path, dst_path
        )
        result = await proc.wait()
        if result:
            raise RuntimeError(f"mount exited with code {result}")

        try:
            if read_only:
                proc = await asyncio.create_subprocess_exec(
                    "mount", "-oremount,ro.bind", "--", dst_path
                )
                result = await proc.wait()
                if result:
                    raise RuntimeError(f"mount exited with code {result}")

            yield
        finally:
            proc = await asyncio.create_subprocess_exec("umount", dst_path)
            result = await proc.wait()
            if result:
                raise RuntimeError(f"umount exited with code {result}")

    async def __aenter__(self) -> "TaskWorkspace":
        if self.exit_stack is not None:
            return self

        self.exit_stack = contextlib.AsyncExitStack()
        await self.exit_stack.enter_async_context(self._setup_dirs())
        await self.exit_stack.enter_async_context(self._mount_overlay())
        for src_path, dst_path, read_only in self.scratch_space:
            await self.exit_stack.enter_async_context(
                self._mount_scratch(src_path, dst_path, read_only)
            )

        return self

    async def __aexit__(self, exc_type, exc_value, exc_tb) -> None:
        if self.exit_stack is None:
            return
        await self.exit_stack.aclose()
        self.exit_stack = None
