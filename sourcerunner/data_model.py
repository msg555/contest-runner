from __future__ import annotations

import abc
import asyncio
import os
import shutil
from typing import Annotated, Literal

from pydantic import BaseModel, Field


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
    build_timeout: float = 5.0
    build_mem_limit_bytes: int = 128 * 2**20


class LanguageManifest(BaseModel):
    languages: dict[str, LanguageConfig]


class SourceDefinition(BaseModel):
    language: str
    file: str

    def post_validate(self, langs: LanguageManifest, task: JudgeTask) -> None:
        if self.language not in langs.languages:
            raise ValueError(f"Unknown language {repr(self.language)} in source")
        if self.file not in task.files:
            raise ValueError(f"Unknown file {repr(self.file)} in source")


class FileReadDefinition(BaseModel):
    type: Literal["read"] = "read"
    file: str

    def post_validate(self, task: JudgeTask) -> None:
        if self.file not in task.files:
            raise ValueError(f"Unknown file {repr(self.file)} in file read descriptor")


class FileWriteDefinition(BaseModel):
    type: Literal["write"] = "write"
    name: str

    def post_validate(self, task: JudgeTask) -> None:
        pass


class PipeDefinition(BaseModel):
    type: Literal["pipe"] = "pipe"
    name: str
    mode: Literal["read", "write", "duplex"]

    def post_validate(self, task: JudgeTask) -> None:
        pass


FileDescriptor = Annotated[
    FileReadDefinition | FileWriteDefinition | PipeDefinition,
    Field(discriminator="type"),
]


class RunDefinition(BaseModel):
    source: str
    time_limit: float
    memory_limit: int
    output_limit: int  # TODO
    fd_map: dict[int, FileDescriptor] = {}
    files_in: dict[str, str] = {}
    files_out: dict[str, str] = {}

    def post_validate(self, task: JudgeTask) -> None:
        if self.source not in task.sources:
            raise ValueError(f"Unknown source {repr(self.source)} in run")
        output_names = set()
        for fdesc in self.fd_map.values():
            fdesc.post_validate(task)
            if isinstance(fdesc, FileWriteDefinition):
                if fdesc.name in output_names:
                    raise ValueError("Duplicate output name {repr(fdesc.name)} in run")
                output_names.add(fdesc.name)
        for output_name, path in self.files_out.items():
            if output_name in output_names:
                raise ValueError("Duplicate output name {repr(fdesc.name)} in run")
            output_names.add(output_name)
            _check_simple_path_element(path)
        for filename, file in self.files_in.items():
            _check_simple_path_element(filename)
            if file not in task.files:
                raise ValueError(f"Unknown file {repr(file)} in files_in")


class StageDefinition(BaseModel):
    runs: list[RunDefinition]

    def post_validate(self, task: JudgeTask) -> None:
        pipe_map: dict[str, list[PipeDefinition]] = {}
        for run in self.runs:
            run.post_validate(task)
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

    def post_validate(self, langs: LanguageManifest) -> None:
        for source in self.sources.values():
            source.post_validate(langs, self)
        for stage in self.stages:
            stage.post_validate(self)
