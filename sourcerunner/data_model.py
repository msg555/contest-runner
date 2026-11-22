from __future__ import annotations

import abc
import asyncio
import os
import shutil
import string
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

from sourcerunner.runc import RuncResult


def _check_simple_path_element(path: str) -> None:
    if "/" in path or "\\" in path:
        raise ValueError(
            f"Simple path element {repr(path)} cannot contain path separators"
        )
    if path in ("", ".", ".."):
        raise ValueError(f"Simple path element {repr(path)} is invalid")


def _check_run_ref(
    task: JudgeTask, ref_stage_index: int, ref_run_index: int, current_stage: int
) -> None:
    if ref_stage_index < 0 or ref_stage_index >= current_stage:
        raise ValueError("Stage index must be an earlier index")
    if ref_run_index < 0 or ref_run_index >= len(task.stages[ref_stage_index].runs):
        raise ValueError("Invalid run_index for stage")


class LanguageConfig(BaseModel):
    runner: str
    extension: str
    build: list[str] | None = None
    run: list[str]
    build_timeout: float = 10.0
    build_mem_limit_bytes: int = 512 * 2**20


class LanguageManifest(BaseModel):
    languages: dict[str, LanguageConfig]


class SourceDefinition(BaseModel):
    language: str
    file: str
    extra_files: dict[str, str] = {}

    def post_validate(self, task: JudgeTask, *, langs: LanguageManifest) -> None:
        lang = langs.languages.get(self.language)
        if lang is None:
            raise ValueError(f"Unknown language {repr(self.language)} in source")
        if self.file not in task.files:
            raise ValueError(f"Unknown file {repr(self.file)} in source")
        for file_path, file in self.extra_files.items():
            _check_simple_path_element(file_path)
            if file_path == f"solution.{lang.extension}":
                raise ValueError(f"extra_file name aliases with source file name")
            if file not in task.files:
                raise ValueError(f"Unknown file in extra_files {repr(file)} in source")


class FileReadDefinition(BaseModel):
    type: Literal["read"] = "read"
    file: str

    def post_validate(self, task: JudgeTask, **context) -> None:
        if self.file not in task.files:
            raise ValueError(f"Unknown file {repr(self.file)} in file read descriptor")


class FileRunReadDefinition(BaseModel):
    type: Literal["run"] = "run"
    name: str
    stage_index: int
    run_index: int = 0
    ignore_if_missing: bool = False

    @field_validator("name")
    @classmethod
    def valid_file_name(cls, v: str) -> str:
        _check_simple_path_element(v)
        return v

    def post_validate(self, task: JudgeTask, *, stage_index: int, **context) -> None:
        _check_run_ref(task, self.stage_index, self.run_index, stage_index)


class RunRef(BaseModel):
    stage_index: int
    run_index: int = 0

    def post_validate(self, task: JudgeTask, *, stage_index: int, **context) -> None:
        _check_run_ref(task, self.stage_index, self.run_index, stage_index)


class FileRunStatusDefinition(BaseModel):
    """
    Provides the status of an earlier run. Without `format` the contents are
    the RunStatus as JSON. With `format` the contents are the format string
    rendered over the fields in STATUS_FORMAT_FIELDS plus a trailing newline.
    """

    type: Literal["status"] = "status"
    stage_index: int
    run_index: int = 0
    format: str | None = None

    @field_validator("format")
    @classmethod
    def valid_format(cls, v: str | None) -> str | None:
        if v is not None:
            _check_status_format(v)
        return v

    def post_validate(self, task: JudgeTask, *, stage_index: int, **context) -> None:
        _check_run_ref(task, self.stage_index, self.run_index, stage_index)


class FileWriteDefinition(BaseModel):
    type: Literal["write"] = "write"
    name: str
    export: bool = False

    def post_validate(self, task: JudgeTask, **context) -> None:
        pass


class PipeDefinition(BaseModel):
    type: Literal["pipe"] = "pipe"
    name: str
    mode: Literal["read", "write", "duplex"]

    def post_validate(self, task: JudgeTask, **context) -> None:
        pass


class GeneratedDefinition(BaseModel):
    type: Literal["generated"] = "generated"
    stage_index: int
    run_index: int
    output_name: str

    def post_validate(self, task: JudgeTask, **context) -> None:
        pass


FileInput = Annotated[
    FileReadDefinition | FileRunReadDefinition | FileRunStatusDefinition,
    Field(discriminator="type"),
]

FileDescriptor = Annotated[
    FileInput | FileWriteDefinition | PipeDefinition,
    Field(discriminator="type"),
]


class RunDefinition(BaseModel):
    source: str
    time_limit: float
    memory_limit: int
    output_limit: int  # TODO
    fd_map: dict[int, FileDescriptor] = {}
    files_in: dict[str, FileInput] = {}
    files_out: dict[str, FileWriteDefinition] = {}
    args: list[str] = []
    depends_on: list[RunRef] = []

    def post_validate(self, task: JudgeTask, **context) -> None:
        if self.source not in task.sources:
            raise ValueError(f"Unknown source {repr(self.source)} in run")
        for ref in self.depends_on:
            ref.post_validate(task, **context)
        output_names = set()
        for fdesc in self.fd_map.values():
            fdesc.post_validate(task, **context)
            if isinstance(fdesc, FileWriteDefinition):
                if fdesc.name in output_names:
                    raise ValueError(f"Duplicate output name {repr(fdesc.name)} in run")
                output_names.add(fdesc.name)
        for path, file_out in self.files_out.items():
            file_out.post_validate(task, **context)
            _check_simple_path_element(path)
            if file_out.name in output_names:
                raise ValueError(f"Duplicate output name {repr(file_out.name)} in run")
            output_names.add(file_out.name)
        for filename, fdesc in self.files_in.items():
            _check_simple_path_element(filename)
            fdesc.post_validate(task, **context)


class StageDefinition(BaseModel):
    runs: list[RunDefinition]

    def post_validate(self, task: JudgeTask, **context) -> None:
        pipe_map: dict[str, list[PipeDefinition]] = {}
        for run_index, run in enumerate(self.runs):
            run.post_validate(task, run_index=run_index, **context)
            for fdesc in run.fd_map.values():
                if isinstance(fdesc, PipeDefinition):
                    pipe_map.setdefault(fdesc.name, []).append(fdesc)

        for pipe_name, pipe_descs in pipe_map.items():
            if len(pipe_descs) == 1:
                raise ValueError(f"Only one pipe connected to pipe {repr(pipe_name)}")
            if len(pipe_descs) > 2:
                raise ValueError(
                    f"More than two pipes connected to pipe {repr(pipe_name)}"
                )
            modes = list({pipe_descs[0].mode, pipe_descs[1].mode})
            if len(modes) == 1:
                if "duplex" not in modes:
                    raise ValueError(
                        f"Expected read/write ends for {repr(pipe_name)}. Found two {modes[0]} ends"
                    )
            elif "duplex" in modes:
                raise ValueError(
                    f"Unexpected mix of duplex and read/write pipe modes for {repr(pipe_name)}"
                )


class FileInputBase(abc.ABC, BaseModel):
    @abc.abstractmethod
    async def open_fd(self) -> int:
        pass

    @abc.abstractmethod
    async def write_to(self, path: str) -> None:
        pass


class FileSystemFile(FileInputBase):
    type: Literal["fs"] = "fs"
    path: str

    async def open_fd(self) -> int:
        fd = await asyncio.get_running_loop().run_in_executor(
            None, os.open, self.path, os.O_RDONLY
        )
        os.set_inheritable(fd, True)
        return fd

    async def write_to(self, path: str) -> None:
        await asyncio.get_running_loop().run_in_executor(
            None, shutil.copy, self.path, path
        )


class InlineFile(FileInputBase):
    type: Literal["inline"] = "inline"
    contents: str
    encoding: str = "utf-8"

    async def open_fd(self) -> int:
        def sync_open() -> int:
            pipe = os.pipe()
            os.write(pipe[1], self.contents.encode(self.encoding))
            os.close(pipe[1])
            os.set_inheritable(pipe[0], True)
            return pipe[0]

        return await asyncio.get_running_loop().run_in_executor(None, sync_open)

    async def write_to(self, path: str) -> None:
        def sync_write():
            with open(path, "w", encoding=self.encoding) as fdata:
                fdata.write(self.contents)

        await asyncio.get_running_loop().run_in_executor(None, sync_write)


FileDataInput = Annotated[
    FileSystemFile | InlineFile,
    Field(discriminator="type"),
]


class JudgeTask(BaseModel):
    files: dict[str, FileDataInput]
    sources: dict[str, SourceDefinition]
    stages: list[StageDefinition]

    def post_validate(self, langs: LanguageManifest, **context) -> None:
        for source in self.sources.values():
            source.post_validate(self, langs=langs, **context)
        for stage_index, stage in enumerate(self.stages):
            stage.post_validate(self, stage_index=stage_index, **context)


class BuildStatus(BaseModel):
    source: str
    state: Literal["success", "failed", "error"]
    result: RuncResult | None = None
    log: str | None = None
    error: str | None = None


class RunStatus(BaseModel):
    stage_index: int
    run_index: int
    state: Literal["success", "failed", "skipped", "error"]
    reason: str | None = None
    result: RuncResult | None = None

    def format_fields(self) -> dict[str, Any]:
        result = self.result
        return {
            "state": self.state,
            "reason": self.reason,
            "exit_type": result.exit_type if result else None,
            "exit_status": result.exit_status if result else None,
            "signal": result.signal if result else None,
            "error": result.error if result else None,
            "kill_reason": result.kill_reason if result else None,
            "time_cpu": result.time_cpu if result else None,
            "time_sys": result.time_sys if result else None,
            "time_wall": result.time_wall if result else None,
            "mem_peak": result.mem_peak if result else None,
        }


class TaskResult(BaseModel):
    builds: dict[str, BuildStatus]
    runs: list[list[RunStatus]]


# Representative values for each status format field, used to check format
# specs at validation time.
_STATUS_FORMAT_SAMPLE: dict[str, Any] = {
    "state": "success",
    "reason": "reason",
    "exit_type": "normal",
    "exit_status": 0,
    "signal": 9,
    "error": "error",
    "kill_reason": "TIMEOUT_CPU",
    "time_cpu": 0.5,
    "time_sys": 0.5,
    "time_wall": 0.5,
    "mem_peak": 1024,
}
STATUS_FORMAT_FIELDS = frozenset(_STATUS_FORMAT_SAMPLE)
STATUS_MISSING_VALUE = "-"


class _StatusFormatter(string.Formatter):
    def format_field(self, value: Any, format_spec: str) -> Any:
        if value is None:
            return STATUS_MISSING_VALUE
        return super().format_field(value, format_spec)


def _check_status_format(fmt: str) -> None:
    for _, field_name, format_spec, conversion in string.Formatter().parse(fmt):
        if field_name is None:
            continue
        if field_name not in STATUS_FORMAT_FIELDS:
            raise ValueError(
                f"Unknown status format field {repr(field_name)}; expected one of "
                f"{', '.join(sorted(STATUS_FORMAT_FIELDS))}"
            )
        if conversion:
            raise ValueError("Conversions are not supported in status formats")
        if format_spec and "{" in format_spec:
            raise ValueError("Nested fields are not supported in status formats")
    try:
        _StatusFormatter().vformat(fmt, (), _STATUS_FORMAT_SAMPLE)
    except ValueError as exc:
        raise ValueError(f"Invalid status format {repr(fmt)}: {exc}") from exc


def render_run_status(status: RunStatus, fmt: str | None) -> str:
    if fmt is None:
        return status.model_dump_json() + "\n"
    return _StatusFormatter().vformat(fmt, (), status.format_fields()) + "\n"
