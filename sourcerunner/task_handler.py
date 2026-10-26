import asyncio
import contextlib
import os
import shutil
import sys
import uuid

import yaml

from sourcerunner.data_model import (
    FileReadDefinition,
    FileWriteDefinition,
    JudgeTask,
    LanguageManifest,
    PipeDefinition,
)
from sourcerunner.mount import TaskWorkspace
from sourcerunner.runc import FDContainer, RuncRunner


@contextlib.asynccontextmanager
async def _temp_dir(owner: tuple[int, int] | None = None):
    temp_dir = os.path.join("/mnt/sourcerunner/tmp", str(uuid.uuid4()))

    def _setup_dir():
        os.makedirs(temp_dir)
        if owner is not None:
            os.chown(temp_dir, owner[0], owner[1])

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _setup_dir)
    yield temp_dir
    await loop.run_in_executor(None, shutil.rmtree, temp_dir)


class TaskHandler:
    def __init__(self, langs: LanguageManifest) -> None:
        self.langs = langs

    async def handle_task(self, task: JudgeTask) -> None:
        async with contextlib.AsyncExitStack() as stack:
            compiled_data_paths = {}

            # Build all sources that require it
            for source_name, source in task.sources.items():
                lang = self.langs.languages[source.language]

                if lang.build is not None:
                    async with TaskWorkspace(lang.runner, read_only=True) as workspace:
                        compiled_data_path = await stack.enter_async_context(
                            _temp_dir(owner=(1000, 1000)),
                        )
                        await workspace.add_scratch(
                            "/solution",
                            src_path=compiled_data_path,
                            read_only=False,
                        )
                        solution_path = os.path.join(
                            compiled_data_path, f"solution.{lang.extension}"
                        )
                        await task.files[source.file].write_to(solution_path)

                        build_runner = RuncRunner(
                            workspace.mount_dir,
                            cwd="/solution",
                            args=lang.build,
                            timeout_wall=lang.build_timeout,
                            mem_limit_bytes=lang.build_mem_limit_bytes,
                        )
                        build_result = await build_runner.run()
                        if (
                            build_result.exit_type != "normal"
                            or build_result.exit_status != 0
                        ):
                            raise Exception(f"Failed to build source {source_name}")

                        compiled_data_paths[source_name] = compiled_data_path

            for stage in task.stages:
                async with contextlib.AsyncExitStack() as stage_stack:
                    pipes: dict[str, list[FDContainer]] = {}
                    for run in stage.runs:
                        for fdesc in run.fd_map.values():
                            if not isinstance(fdesc, PipeDefinition):
                                continue
                            if fdesc.name in pipes:
                                continue
                            if fdesc.mode == "duplex":
                                pipe_fds = list(FDContainer.socketpair())
                            else:
                                pipe_fds = list(FDContainer.pipe())
                            pipes[fdesc.name] = pipe_fds
                            stage_stack.push(pipe_fds[0])
                            stage_stack.push(pipe_fds[1])

                    async def do_stage_run(run) -> None:
                        source = task.sources[run.source]
                        lang = self.langs.languages[source.language]

                        async with (
                            TaskWorkspace(lang.runner, read_only=True) as workspace,
                            _temp_dir() as output_dir,
                        ):
                            if run.source in compiled_data_paths:
                                await workspace.add_scratch(
                                    "/solution",
                                    src_path=compiled_data_paths[run.source],
                                    read_only=True,
                                )

                            task_path = await workspace.add_scratch("/task")
                            for in_path, in_file in run.files_in.items():
                                await task.files[in_file].write_to(
                                    os.path.join(task_path, in_path)
                                )

                            fd_map: dict[int, FDContainer] = {}
                            for fd_num, fdesc in run.fd_map.items():
                                if isinstance(fdesc, FileReadDefinition):
                                    fd_map[fd_num] = stage_stack.push(
                                        FDContainer(
                                            await task.files[fdesc.file].open_fd()
                                        )
                                    )
                                elif isinstance(fdesc, FileWriteDefinition):
                                    raw_fd = os.open(
                                        os.path.join(output_dir, fdesc.name),
                                        os.O_CREAT | os.O_WRONLY,
                                    )
                                    fd_map[fd_num] = stage_stack.push(
                                        FDContainer(raw_fd)
                                    )
                                elif isinstance(fdesc, PipeDefinition):
                                    if fdesc.mode == "read":
                                        fd_map[fd_num] = pipes[fdesc.name][0]
                                    elif fdesc.mode == "write":
                                        fd_map[fd_num] = pipes[fdesc.name][1]
                                    else:
                                        fd_map[fd_num] = pipes[fdesc.name].pop()
                                else:
                                    raise RuntimeError("Unhandled fd_map type")

                            runner = RuncRunner(
                                workspace.mount_dir,
                                cwd="/task",
                                args=lang.run,
                                timeout_cpu=run.time_limit,
                                timeout_wall=run.time_limit + 1,
                                mem_limit_bytes=run.memory_limit,
                            )
                            run_result = await runner.run(
                                fd_map=fd_map,  # type: ignore
                                close_fds=list(fd_map.values())
                            )
                            print(run_result)

                            for path in os.listdir(output_dir):
                                print("Found:", path)
                                with open(
                                    os.path.join(output_dir, path), "rb"
                                ) as fdata:
                                    sys.stdout.buffer.write(fdata.read())

                    await asyncio.gather(*(do_stage_run(run) for run in stage.runs))


async def main():
    with open("language_manifest.yaml", "r", encoding="utf-8") as flang:
        langs = LanguageManifest.model_validate(yaml.safe_load(flang))

    handler = TaskHandler(langs)
    for task_path in sys.argv[1:]:
        with open(task_path, "r", encoding="utf-8") as ftask:
            task = JudgeTask.model_validate(yaml.safe_load(ftask))
        task.post_validate(langs)

        await handler.handle_task(task)


if __name__ == "__main__":
    asyncio.run(main())
