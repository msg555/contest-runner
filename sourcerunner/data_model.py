from __future__ import annotations

import abc
import asyncio
import os
import shutil
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


def _check_simple_path_element(path: str) -> None:
    if "/" in path or "\\" in path:
        raise ValueError(
            f"Simple path element {repr(path)} cannot contain path separators"
        )
    if path in ("", ".", ".."):
        raise ValueError(f"Simple path element {repr(path)} is invalid")


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
        if self.stage_index < 0 or self.stage_index >= stage_index:
            raise ValueError("Stage index must be an earlier index")
        if self.run_index < 0 or self.run_index >= len(
            task.stages[self.stage_index].runs
        ):
            raise ValueError("Invalid run_index for stage")


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
    FileReadDefinition | FileRunReadDefinition,
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

    def post_validate(self, task: JudgeTask, **context) -> None:
        if self.source not in task.sources:
            raise ValueError(f"Unknown source {repr(self.source)} in run")
        output_names = set()
        for fdesc in self.fd_map.values():
            fdesc.post_validate(task, **context)
            if isinstance(fdesc, FileWriteDefinition):
                if fdesc.name in output_names:
                    raise ValueError("Duplicate output name {repr(fdesc.name)} in run")
                output_names.add(fdesc.name)
        for path, file_out in self.files_out.items():
            file_out.post_validate(task, **context)
            _check_simple_path_element(path)
            if file_out.name in output_names:
                raise ValueError("Duplicate output name {repr(fdesc.name)} in run")
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


FileInput = Annotated[
    FileSystemFile | InlineFile,
    Field(discriminator="type"),
]


class JudgeTask(BaseModel):
    files: dict[str, FileInput]
    sources: dict[str, SourceDefinition]
    stages: list[StageDefinition]

    def post_validate(self, langs: LanguageManifest, **context) -> None:
        for source in self.sources.values():
            source.post_validate(self, langs=langs, **context)
        for stage_index, stage in enumerate(self.stages):
            stage.post_validate(self, stage_index=stage_index, **context)
