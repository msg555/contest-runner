import asyncio
import contextlib
import json
import os
import shutil
import sys
import uuid
from typing import Any, Protocol, TextIO

import yaml

from sourcerunner.data_model import (
    BuildStatus,
    FileReadDefinition,
    FileRunReadDefinition,
    FileRunStatusDefinition,
    FileWriteDefinition,
    JudgeTask,
    LanguageConfig,
    LanguageManifest,
    PipeDefinition,
    RunDefinition,
    RunStatus,
    SourceDefinition,
    TaskResult,
    render_run_status,
)
from sourcerunner.mount import TaskWorkspace
from sourcerunner.runc import FDContainer, RuncExecutionError, RuncResult, RuncRunner

BUILD_LOG_LIMIT = 64 * 2**10

RuncFDMap = dict[int, int | FDContainer | tuple[str, bool]]


class TaskReporter(Protocol):
    """
    Receives progress events from TaskHandler. Skipped runs receive
    `run_finished` without a preceding `run_started`.
    """

    async def build_started(self, source: str) -> None: ...

    async def build_finished(self, status: BuildStatus) -> None: ...

    async def stage_started(self, stage_index: int) -> None: ...

    async def run_started(self, stage_index: int, run_index: int) -> None: ...

    async def run_finished(self, status: RunStatus, exports: dict[str, str]) -> None:
        """
        `exports` maps each exported output name to a host path. The path may
        not exist if the run did not create the output, and is only valid for
        the duration of this call.
        """

    async def task_finished(self, result: TaskResult) -> None: ...


class NullReporter:
    async def build_started(self, source: str) -> None:
        pass

    async def build_finished(self, status: BuildStatus) -> None:
        pass

    async def stage_started(self, stage_index: int) -> None:
        pass

    async def run_started(self, stage_index: int, run_index: int) -> None:
        pass

    async def run_finished(self, status: RunStatus, exports: dict[str, str]) -> None:
        pass

    async def task_finished(self, result: TaskResult) -> None:
        pass


class JsonLinesReporter:
    """Writes one JSON object per event, inlining exported file contents."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stdout

    def _emit(self, event: str, **data: Any) -> None:
        self.stream.write(json.dumps({"event": event, **data}) + "\n")
        self.stream.flush()

    async def build_started(self, source: str) -> None:
        self._emit("build_started", source=source)

    async def build_finished(self, status: BuildStatus) -> None:
        self._emit("build_finished", status=status.model_dump(mode="json"))

    async def stage_started(self, stage_index: int) -> None:
        self._emit("stage_started", stage_index=stage_index)

    async def run_started(self, stage_index: int, run_index: int) -> None:
        self._emit("run_started", stage_index=stage_index, run_index=run_index)

    async def run_finished(self, status: RunStatus, exports: dict[str, str]) -> None:
        def _read_exports() -> dict[str, str | None]:
            contents: dict[str, str | None] = {}
            for name, path in exports.items():
                try:
                    with open(path, "rb") as fdata:
                        contents[name] = fdata.read().decode("utf-8", "replace")
                except FileNotFoundError:
                    contents[name] = None
            return contents

        contents = await asyncio.get_running_loop().run_in_executor(None, _read_exports)
        self._emit(
            "run_finished", status=status.model_dump(mode="json"), exports=contents
        )

    async def task_finished(self, result: TaskResult) -> None:
        self._emit("task_finished", result=result.model_dump(mode="json"))


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


def _output_path(output_dir: str, stage_index: int, run_index: int, name: str) -> str:
    return os.path.join(output_dir, f"{stage_index}.{run_index}.{name}")


async def _write_text(path: str, contents: str) -> None:
    def _sync_write() -> None:
        with open(path, "w", encoding="utf-8") as fdata:
            fdata.write(contents)

    await asyncio.get_running_loop().run_in_executor(None, _sync_write)


def _read_log(path: str) -> str:
    with open(path, "rb") as flog:
        data = flog.read(BUILD_LOG_LIMIT + 1)
    log = data[:BUILD_LOG_LIMIT].decode("utf-8", "replace")
    if len(data) > BUILD_LOG_LIMIT:
        log += "\n<truncated>"
    return log


def _run_succeeded(result: RuncResult) -> bool:
    return result.exit_type == "normal" and result.exit_status == 0


class TaskHandler:
    def __init__(
        self, langs: LanguageManifest, reporter: TaskReporter | None = None
    ) -> None:
        self.langs = langs
        self.reporter: TaskReporter = reporter or NullReporter()

    async def handle_task(self, task: JudgeTask) -> TaskResult:
        async with (
            contextlib.AsyncExitStack() as stack,
            _temp_dir() as output_dir,
        ):
            builds: dict[str, BuildStatus] = {}
            compiled_data_paths: dict[str, str] = {}

            # Build all sources that require it
            for source_index, (source_name, source) in enumerate(task.sources.items()):
                lang = self.langs.languages[source.language]
                if lang.build is None:
                    continue

                await self.reporter.build_started(source_name)
                compiled_data_path = await stack.enter_async_context(
                    _temp_dir(owner=(1000, 1000)),
                )
                status = await self._build_source(
                    task,
                    source_name,
                    source,
                    lang,
                    compiled_data_path,
                    os.path.join(output_dir, f"build.{source_index}.log"),
                )
                builds[source_name] = status
                if status.state == "success":
                    compiled_data_paths[source_name] = compiled_data_path
                await self.reporter.build_finished(status)

            statuses: list[list[RunStatus]] = []
            for stage_index in range(len(task.stages)):
                await self.reporter.stage_started(stage_index)
                statuses.append(
                    await self._run_stage(
                        task,
                        stage_index,
                        builds,
                        compiled_data_paths,
                        statuses,
                        output_dir,
                    )
                )

            result = TaskResult(builds=builds, runs=statuses)
            await self.reporter.task_finished(result)
            return result

    async def _build_source(
        self,
        task: JudgeTask,
        source_name: str,
        source: SourceDefinition,
        lang: LanguageConfig,
        compiled_data_path: str,
        log_path: str,
    ) -> BuildStatus:
        assert lang.build is not None
        async with TaskWorkspace(lang.runner, read_only=True) as workspace:
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

            # stdout and stderr share one log file. They need distinct fds
            # for the supervisor's fd remapping.
            log_out = FDContainer(
                os.open(log_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
            )
            log_err = FDContainer.dup(log_out)
            try:
                build_result = await build_runner.run(
                    fd_map={1: log_out, 2: log_err},
                    close_fds=[log_out, log_err],
                    inherit_stdio=False,
                )
            except RuncExecutionError as exc:
                return BuildStatus(source=source_name, state="error", error=str(exc))
            finally:
                log_out.close()
                log_err.close()

            log = await asyncio.get_running_loop().run_in_executor(
                None, _read_log, log_path
            )
            return BuildStatus(
                source=source_name,
                state="success" if _run_succeeded(build_result) else "failed",
                result=build_result,
                log=log,
            )

    @staticmethod
    def _skip_reason(
        run: RunDefinition,
        builds: dict[str, BuildStatus],
        statuses: list[list[RunStatus]],
        output_dir: str,
    ) -> str | None:
        build = builds.get(run.source)
        if build is not None and build.state != "success":
            return f"build failed: {run.source}"

        for ref in run.depends_on:
            dep = statuses[ref.stage_index][ref.run_index]
            if dep.state != "success":
                return (
                    f"dependency stage {ref.stage_index} run {ref.run_index} "
                    f"was {dep.state}"
                )

        for fdesc in (*run.files_in.values(), *run.fd_map.values()):
            if not isinstance(fdesc, FileRunReadDefinition) or fdesc.ignore_if_missing:
                continue
            path = _output_path(
                output_dir, fdesc.stage_index, fdesc.run_index, fdesc.name
            )
            if not os.path.exists(path):
                dep = statuses[fdesc.stage_index][fdesc.run_index]
                return (
                    f"missing input {repr(fdesc.name)} from stage "
                    f"{fdesc.stage_index} run {fdesc.run_index} ({dep.state})"
                )

        return None

    async def _run_stage(
        self,
        task: JudgeTask,
        stage_index: int,
        builds: dict[str, BuildStatus],
        compiled_data_paths: dict[str, str],
        statuses: list[list[RunStatus]],
        output_dir: str,
    ) -> list[RunStatus]:
        stage = task.stages[stage_index]
        async with contextlib.AsyncExitStack() as stage_stack:
            # Create pipes. Ends are assigned per (run_index, fd_num) in order
            # of appearance so duplex assignment is deterministic.
            pipe_groups: dict[str, list[tuple[int, int, PipeDefinition]]] = {}
            for run_index, run in enumerate(stage.runs):
                for fd_num, fdesc in run.fd_map.items():
                    if isinstance(fdesc, PipeDefinition):
                        pipe_groups.setdefault(fdesc.name, []).append(
                            (run_index, fd_num, fdesc)
                        )

            pipe_ends: dict[tuple[int, int], FDContainer] = {}
            for ends in pipe_groups.values():
                if ends[0][2].mode == "duplex":
                    pair = FDContainer.socketpair()
                    for (run_index, fd_num, _), end in zip(ends, pair):
                        pipe_ends[(run_index, fd_num)] = end
                else:
                    read_end, write_end = FDContainer.pipe()
                    for run_index, fd_num, fdesc in ends:
                        pipe_ends[(run_index, fd_num)] = (
                            read_end if fdesc.mode == "read" else write_end
                        )
            for end in pipe_ends.values():
                stage_stack.push(end)

            async def run_one(run_index: int, run: RunDefinition) -> RunStatus:
                run_pipes = [
                    end for (ri, _), end in pipe_ends.items() if ri == run_index
                ]

                def close_pipes() -> None:
                    # Lets the peer see EOF/EPIPE instead of blocking.
                    for end in run_pipes:
                        end.close()

                reason = self._skip_reason(run, builds, statuses, output_dir)
                if reason is not None:
                    close_pipes()
                    status = RunStatus(
                        stage_index=stage_index,
                        run_index=run_index,
                        state="skipped",
                        reason=reason,
                    )
                    await self.reporter.run_finished(status, {})
                    return status

                await self.reporter.run_started(stage_index, run_index)
                exports: dict[str, str] = {}
                try:
                    status = await self._execute_run(
                        task,
                        stage_index,
                        run_index,
                        run,
                        compiled_data_paths,
                        statuses,
                        pipe_ends,
                        stage_stack,
                        output_dir,
                        exports,
                    )
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    close_pipes()
                    status = RunStatus(
                        stage_index=stage_index,
                        run_index=run_index,
                        state="error",
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                await self.reporter.run_finished(status, exports)
                return status

            return list(
                await asyncio.gather(
                    *(
                        run_one(run_index, run)
                        for run_index, run in enumerate(stage.runs)
                    )
                )
            )

    async def _execute_run(
        self,
        task: JudgeTask,
        stage_index: int,
        run_index: int,
        run: RunDefinition,
        compiled_data_paths: dict[str, str],
        statuses: list[list[RunStatus]],
        pipe_ends: dict[tuple[int, int], FDContainer],
        stage_stack: contextlib.AsyncExitStack,
        output_dir: str,
        exports: dict[str, str],
    ) -> RunStatus:
        source = task.sources[run.source]
        lang = self.langs.languages[source.language]
        loop = asyncio.get_running_loop()

        async with TaskWorkspace(lang.runner, read_only=True) as workspace:
            if run.source in compiled_data_paths:
                await workspace.add_scratch(
                    "/solution",
                    src_path=compiled_data_paths[run.source],
                    read_only=True,
                )

            task_path = await workspace.add_scratch(
                "/task",
                src_path_owner=(1000, 1000),
            )
            for in_path, in_file in run.files_in.items():
                dst_path = os.path.join(task_path, in_path)
                if isinstance(in_file, FileReadDefinition):
                    await task.files[in_file.file].write_to(dst_path)
                elif isinstance(in_file, FileRunReadDefinition):
                    path = _output_path(
                        output_dir,
                        in_file.stage_index,
                        in_file.run_index,
                        in_file.name,
                    )
                    # Missing required inputs were handled by _skip_reason.
                    if os.path.exists(path):
                        await loop.run_in_executor(None, shutil.copy, path, dst_path)
                elif isinstance(in_file, FileRunStatusDefinition):
                    await _write_text(
                        dst_path,
                        render_run_status(
                            statuses[in_file.stage_index][in_file.run_index],
                            in_file.format,
                        ),
                    )
                else:
                    raise RuntimeError("Unhandled in_file type")

            fd_map: RuncFDMap = {}
            for fd_num, fdesc in run.fd_map.items():
                if isinstance(fdesc, FileReadDefinition):
                    fd_map[fd_num] = stage_stack.push(
                        FDContainer(await task.files[fdesc.file].open_fd())
                    )
                elif isinstance(fdesc, FileRunReadDefinition):
                    path = _output_path(
                        output_dir, fdesc.stage_index, fdesc.run_index, fdesc.name
                    )
                    if os.path.exists(path):
                        fd_map[fd_num] = stage_stack.push(
                            FDContainer(
                                await loop.run_in_executor(
                                    None, os.open, path, os.O_RDONLY
                                )
                            )
                        )
                    else:
                        # ignore_if_missing: provide an empty stream.
                        read_end, write_end = FDContainer.pipe()
                        write_end.close()
                        fd_map[fd_num] = stage_stack.push(read_end)
                elif isinstance(fdesc, FileRunStatusDefinition):
                    status_path = os.path.join(
                        output_dir, f"status.{stage_index}.{run_index}.{fd_num}"
                    )
                    await _write_text(
                        status_path,
                        render_run_status(
                            statuses[fdesc.stage_index][fdesc.run_index],
                            fdesc.format,
                        ),
                    )
                    fd_map[fd_num] = stage_stack.push(
                        FDContainer(
                            await loop.run_in_executor(
                                None, os.open, status_path, os.O_RDONLY
                            )
                        )
                    )
                elif isinstance(fdesc, FileWriteDefinition):
                    if fdesc.export:
                        exports[fdesc.name] = _output_path(
                            output_dir, stage_index, run_index, fdesc.name
                        )
                    raw_fd = os.open(
                        _output_path(output_dir, stage_index, run_index, fdesc.name),
                        os.O_CREAT | os.O_WRONLY,
                    )
                    fd_map[fd_num] = stage_stack.push(FDContainer(raw_fd))
                elif isinstance(fdesc, PipeDefinition):
                    fd_map[fd_num] = pipe_ends[(run_index, fd_num)]
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
                fd_map=fd_map,
                close_fds=[fd for fd in fd_map.values() if isinstance(fd, FDContainer)],
                inherit_stdio=False,
            )

            # TODO: Make async compatible
            for path, file_out in run.files_out.items():
                out_path = _output_path(
                    output_dir, stage_index, run_index, file_out.name
                )
                if file_out.export:
                    exports[file_out.name] = out_path
                try:
                    os.rename(os.path.join(task_path, path), out_path)
                except FileNotFoundError:
                    pass

        return RunStatus(
            stage_index=stage_index,
            run_index=run_index,
            state="success" if _run_succeeded(run_result) else "failed",
            result=run_result,
        )


async def main():
    with open("language_manifest.yaml", "r", encoding="utf-8") as flang:
        langs = LanguageManifest.model_validate(yaml.safe_load(flang))

    handler = TaskHandler(langs, JsonLinesReporter())
    for task_path in sys.argv[1:]:
        with open(task_path, "r", encoding="utf-8") as ftask:
            task = JudgeTask.model_validate(yaml.safe_load(ftask))
        task.post_validate(langs)

        await handler.handle_task(task)


if __name__ == "__main__":
    asyncio.run(main())
