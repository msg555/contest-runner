import asyncio
import contextlib
import os
import shutil
import sys
import uuid

import yaml

from sourcerunner.data_model import (
    FileReadDefinition,
    FileRunReadDefinition,
    FileWriteDefinition,
    JudgeTask,
    LanguageManifest,
    PipeDefinition,
    RunDefinition,
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
        async with (
            contextlib.AsyncExitStack() as stack,
            _temp_dir() as output_dir,
        ):
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
                        for extra_file_path, extra_file in source.extra_files.items():
                            await task.files[extra_file].write_to(
                                os.path.join(compiled_data_path, extra_file_path)
                            )

                        build_runner = RuncRunner(
                            workspace.mount_dir,
                            cwd="/solution",
                            args=lang.build,
                            timeout_wall=lang.build_timeout,
                            mem_limit_bytes=lang.build_mem_limit_bytes,
                        )
                        build_result = await build_runner.run(
                            inherit_stdio=False,
                        )
                        if (
                            build_result.exit_type != "normal"
                            or build_result.exit_status != 0
                        ):
                            raise Exception(
                                f"Failed to build source {source_name}: {build_result}"
                            )

                        compiled_data_paths[source_name] = compiled_data_path

            for stage_index, stage in enumerate(task.stages):
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

                    async def do_stage_run(run_index: int, run: RunDefinition) -> None:
                        source = task.sources[run.source]
                        lang = self.langs.languages[source.language]

                        async with TaskWorkspace(
                            lang.runner, read_only=True
                        ) as workspace:
                            if run.source in compiled_data_paths:
                                await workspace.add_scratch(
                                    "/solution",
                                    src_path=compiled_data_paths[run.source],
                                    read_only=True,
                                )

                            task_path = await workspace.add_scratch("/task")
                            for in_path, in_file in run.files_in.items():
                                if isinstance(in_file, FileReadDefinition):
                                    await task.files[in_file.file].write_to(
                                        os.path.join(task_path, in_path)
                                    )
                                elif isinstance(in_file, FileRunReadDefinition):
                                    path = os.path.join(
                                        output_dir,
                                        f"{in_file.stage_index}.{in_file.run_index}.{in_file.name}",
                                    )
                                    if os.path.exists(path):
                                        await asyncio.get_running_loop().run_in_executor(
                                            None,
                                            shutil.copy,
                                            path,
                                            os.path.join(task_path, in_path),
                                        )
                                else:
                                    raise RuntimeError("Unhandled in_file type")

                            fd_map: dict[int, FDContainer] = {}
                            export_files = set()
                            for fd_num, fdesc in run.fd_map.items():
                                if isinstance(fdesc, FileReadDefinition):
                                    fd_map[fd_num] = stage_stack.push(
                                        FDContainer(
                                            await task.files[fdesc.file].open_fd()
                                        )
                                    )
                                elif isinstance(fdesc, FileRunReadDefinition):
                                    path = os.path.join(
                                        output_dir,
                                        f"{fdesc.stage_index}.{fdesc.run_index}.{fdesc.name}",
                                    )
                                    if os.path.exists(path):
                                        fd_map[fd_num] = stage_stack.push(
                                            FDContainer(
                                                await asyncio.get_running_loop().run_in_executor(
                                                    None, os.open, path, os.O_RDONLY
                                                )
                                            )
                                        )
                                elif isinstance(fdesc, FileWriteDefinition):
                                    if fdesc.export:
                                        export_files.add(fdesc.name)
                                    raw_fd = os.open(
                                        os.path.join(
                                            output_dir,
                                            f"{stage_index}.{run_index}.{fdesc.name}",
                                        ),
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
                                args=lang.run + run.args,
                                timeout_cpu=run.time_limit,
                                timeout_wall=run.time_limit + 1,
                                mem_limit_bytes=run.memory_limit,
                            )
                            run_result = await runner.run(
                                fd_map=fd_map,  # type: ignore
                                close_fds=list(fd_map.values()),
                                inherit_stdio=False,
                            )

                            # TODO: Make async compatible
                            for path, file_out in run.files_out.items():
                                if file_out.export:
                                    export_files.add(file_out.name)
                                try:
                                    os.rename(
                                        os.path.join(task_path, path),
                                        os.path.join(
                                            output_dir,
                                            f"{stage_index}.{run_index}.{file_out.name}",
                                        ),
                                    )
                                except FileNotFoundError:
                                    pass

                            for export_file in export_files:
                                try:
                                    print(f"Export {export_file}:")
                                    with open(
                                        os.path.join(
                                            output_dir,
                                            f"{stage_index}.{run_index}.{export_file}",
                                        ),
                                        "rb",
                                    ) as fdata:
                                        sys.stdout.buffer.write(fdata.read())
                                        sys.stdout.buffer.flush()
                                except FileNotFoundError:
                                    print("<not found>")

                    await asyncio.gather(
                        *(
                            do_stage_run(run_index, run)
                            for run_index, run in enumerate(stage.runs)
                        )
                    )


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
