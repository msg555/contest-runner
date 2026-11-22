#!/usr/bin/env python3
"""
Temporary test helper (not part of sourcerunner): converts a problem export
from the legacy contest system (e.g. ./bessieball) into a JudgeTask definition.

Task layout, for N tests (one run per stage; multiple runs per stage are
reserved for interactive tasks that need pipes):
  stage 2i:   submission run for test i (stdio or file I/O per solution).
  stage 2i+1: checker run for test i (static/checker.cpp), `depends_on` the
              submission run. A missing output file skips the checker.
  stage 2N:   scorer run (static/scorer_acwa_status.cpp) reading formatted
              status inputs sol.<i> / chk.<i>; exports `verdict`
              (A/W/T/R/C/E) and `log` (per-test verdicts).

Usage:
  export_to_task.py EXPORT_DIR [-s INDEX] [-o task.yaml]
      Write the task for solution INDEX (default 0) as YAML.
  export_to_task.py EXPORT_DIR --run [-s INDEX | --all]
      Run the task(s) with TaskHandler (inside the source-runner container,
      from the repo root) and compare the final verdict with the solution's
      expected type from config.json.
"""

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import yaml

LANGUAGES = {"C++": "c++"}
EXPECTED_VERDICTS = {"Correct": "A", "Wrong": "W", "TLE": "T", "RTE": "R"}

CHECKER_PATH = "static/checker.cpp"
TESTLIB_PATH = "static/testlib.h"
SCORER_PATH = "static/scorer_acwa_status.cpp"

HELPER_TIME_LIMIT = 30.0
HELPER_MEMORY_LIMIT = 256 * 2**20
OUTPUT_LIMIT = 2**30

SOL_STATUS_FORMAT = "{state} {exit_type} {kill_reason}"
CHK_STATUS_FORMAT = "{state} {exit_type} {exit_status}"


def load_config(export_dir: str) -> dict[str, Any]:
    with open(os.path.join(export_dir, "config.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def fs_file(path: str) -> dict[str, Any]:
    # Relative to the CWD so the task works wherever the repo is mounted.
    return {"type": "fs", "path": os.path.relpath(path)}


def build_task(
    export_dir: str, config: dict[str, Any], solution: dict[str, Any]
) -> dict[str, Any]:
    language = LANGUAGES.get(solution["lang"])
    if language is None:
        raise ValueError(f"Unsupported solution language {solution['lang']!r}")

    files: dict[str, Any] = {
        "sol": fs_file(os.path.join(export_dir, solution["file"])),
        "checker": fs_file(CHECKER_PATH),
        "testlib": fs_file(TESTLIB_PATH),
        "scorer": fs_file(SCORER_PATH),
    }
    sources = {
        "sol": {"language": language, "file": "sol"},
        "checker": {
            "language": "c++",
            "file": "checker",
            "extra_files": {"testlib.h": "testlib"},
        },
        "scorer": {"language": "c++", "file": "scorer"},
    }

    shortname = config["shortname"]
    use_file_io = solution.get("use_file_io", config.get("default_fileio", False))
    time_limit = config["time_limit_ms"] / 1000.0
    memory_limit = config["mem_limit_mb"] * 2**20

    stages: list[dict[str, Any]] = []
    scorer_files_in: dict[str, Any] = {}
    for test_index, test in enumerate(config["tests"]):
        input_id = f"input{test_index}"
        answer_id = f"answer{test_index}"
        files[input_id] = fs_file(os.path.join(export_dir, test["input"]))
        files[answer_id] = fs_file(os.path.join(export_dir, test["output"]))
        sol_stage = len(stages)
        chk_stage = sol_stage + 1

        sol_run: dict[str, Any] = {
            "source": "sol",
            "time_limit": time_limit,
            "memory_limit": memory_limit,
            "output_limit": OUTPUT_LIMIT,
        }
        if use_file_io:
            sol_run["files_in"] = {
                f"{shortname}.in": {"type": "read", "file": input_id}
            }
            sol_run["files_out"] = {
                f"{shortname}.out": {"type": "write", "name": "output"}
            }
        else:
            sol_run["fd_map"] = {
                0: {"type": "read", "file": input_id},
                1: {"type": "write", "name": "output"},
            }
        stages.append({"runs": [sol_run]})

        checker_run = {
            "source": "checker",
            "depends_on": [{"stage_index": sol_stage}],
            "time_limit": HELPER_TIME_LIMIT,
            "memory_limit": HELPER_MEMORY_LIMIT,
            "output_limit": OUTPUT_LIMIT,
            "files_in": {
                "input": {"type": "read", "file": input_id},
                "output": {"type": "run", "stage_index": sol_stage, "name": "output"},
                "answer": {"type": "read", "file": answer_id},
            },
            "fd_map": {2: {"type": "write", "name": "result"}},
            "args": ["input", "output", "answer"],
        }
        stages.append({"runs": [checker_run]})

        scorer_files_in[f"sol.{test_index + 1}"] = {
            "type": "status",
            "stage_index": sol_stage,
            "format": SOL_STATUS_FORMAT,
        }
        scorer_files_in[f"chk.{test_index + 1}"] = {
            "type": "status",
            "stage_index": chk_stage,
            "format": CHK_STATUS_FORMAT,
        }

    scorer_run = {
        "source": "scorer",
        "time_limit": HELPER_TIME_LIMIT,
        "memory_limit": HELPER_MEMORY_LIMIT,
        "output_limit": OUTPUT_LIMIT,
        "files_in": scorer_files_in,
        "fd_map": {
            1: {"type": "write", "name": "verdict", "export": True},
            2: {"type": "write", "name": "log", "export": True},
        },
        "args": [str(len(config["tests"]))],
    }

    stages.append({"runs": [scorer_run]})

    return {"files": files, "sources": sources, "stages": stages}


async def run_task(task_data: dict[str, Any]) -> tuple[str | None, str | None]:
    """Runs a task, returning (verdict, scorer log)."""
    # pylint: disable=import-outside-toplevel
    from sourcerunner.data_model import JudgeTask, LanguageManifest, RunStatus
    from sourcerunner.task_handler import NullReporter, TaskHandler

    with open("language_manifest.yaml", "r", encoding="utf-8") as flang:
        langs = LanguageManifest.model_validate(yaml.safe_load(flang))
    task = JudgeTask.model_validate(task_data)
    task.post_validate(langs)
    scorer_stage = len(task.stages) - 1

    captured: dict[str, str | None] = {"verdict": None, "log": None}

    class Reporter(NullReporter):
        async def build_finished(self, status) -> None:
            print(f"  build {status.source}: {status.state}", file=sys.stderr)
            if status.state != "success" and status.log:
                print(status.log, file=sys.stderr)

        async def run_finished(
            self, status: RunStatus, exports: dict[str, str]
        ) -> None:
            if status.state != "success":
                print(
                    f"  stage {status.stage_index} run {status.run_index}: "
                    f"{status.state} {status.reason or ''}",
                    file=sys.stderr,
                )
            if status.stage_index != scorer_stage:
                return
            for name, path in exports.items():
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        captured[name] = f.read()
                except FileNotFoundError:
                    pass

    await TaskHandler(langs, Reporter()).handle_task(task)
    verdict = captured["verdict"].strip() if captured["verdict"] else None
    return verdict, captured["log"]


async def run_solutions(
    export_dir: str, config: dict[str, Any], indexes: list[int]
) -> int:
    mismatches = 0
    for index in indexes:
        solution = config["solutions"][index]
        if solution["lang"] not in LANGUAGES:
            print(
                f"[{index}] {solution['file']}: skipped "
                f"(unsupported language {solution['lang']!r})"
            )
            continue
        expected = EXPECTED_VERDICTS.get(solution["type"])
        print(f"[{index}] {solution['file']} ({solution['type']})", file=sys.stderr)
        verdict, log = await run_task(build_task(export_dir, config, solution))
        ok = expected is None or verdict == expected
        mismatches += 0 if ok else 1
        print(
            f"[{index}] {solution['file']}: verdict={verdict} "
            f"expected={expected} {'OK' if ok else 'MISMATCH'}"
        )
        if not ok and log:
            print(log, file=sys.stderr)
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("export_dir")
    parser.add_argument("-s", "--solution", type=int, default=0)
    parser.add_argument("--all", action="store_true", help="with --run")
    parser.add_argument("-o", "--output", help="YAML output path (default stdout)")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()

    config = load_config(args.export_dir)

    if args.run:
        indexes = list(range(len(config["solutions"]))) if args.all else [args.solution]
        mismatches = asyncio.run(run_solutions(args.export_dir, config, indexes))
        return 1 if mismatches else 0

    task = build_task(args.export_dir, config, config["solutions"][args.solution])
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fout:
            yaml.safe_dump(task, fout, sort_keys=False)
    else:
        yaml.safe_dump(task, sys.stdout, sort_keys=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
