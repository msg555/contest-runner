# CLAUDE.md

Guidance for AI coding tools working in this repository. This file is
written for tools, not humans: keep it current. Update it whenever a change,
investigation, or user instruction makes any part of it stale or incomplete
(this file is exempt from the change policy below).

## Change policy

- Never modify code unless explicitly asked to. Reviews, questions, and
  investigations are read-only: report findings and leave fixes to the user
  or to a later explicit request.
- When a change is requested, limit edits to the files named in the request.
  Do not touch other files (including "while here" fixes, refactors, or
  formatting) without asking first; report related issues instead.

## Project overview

`sourcerunner` executes untrusted programming-contest submissions (and judge
helpers such as checkers/scorers) inside sandboxed `runc` containers. The
top-level unit of work is a `JudgeTask` (see `sourcerunner/data_model.py`):
a set of input files, sources to build, and an ordered list of stages, each
containing runs that execute concurrently.

**Status:** early WIP. `sourcerunner/task_handler.py` is the module under
active development (see "task_handler status" below).

## Layout

- `sourcerunner/data_model.py` — Pydantic v2 models for `JudgeTask`,
  `LanguageManifest`, and related definitions. Validation happens in two
  phases: `model_validate()` for shape, then `task.post_validate(langs)` for
  cross-references (source/file names, stage indexes, pipe pairing).
- `sourcerunner/task_handler.py` — `TaskHandler(langs, reporter)`;
  `handle_task()` builds sources, runs stages, returns `TaskResult`, and emits
  progress to a `TaskReporter`. `main()` loads `language_manifest.yaml` from
  the CWD and runs each task YAML passed on the command line, printing JSON
  lines via `JsonLinesReporter`.
- `sourcerunner/mount.py` — `TaskWorkspace`: async context manager that creates
  an overlayfs root from `/runners/lower` + `/runners/<runner>` under
  `/mnt/sourcerunner/<uuid>`, plus bind-mounted "scratch" dirs (e.g. `/task`,
  `/solution`).
- `sourcerunner/runc.py` — `RuncRunner` builds an OCI config from
  `runc-base-config.json` (memory/CPU/pids/blkio limits) and spawns the
  supervisor as a subprocess. `FDContainer` wraps raw fds (pipe/socketpair/dup).
  Returns a `RuncResult` (`exit_type`: normal/killed/oom/signal/unknown/supervisor).
- `sourcerunner/supervisor.py` — run as `python -m sourcerunner.supervisor`.
  Remaps fds, does `runc create/start/delete`, enforces CPU and wall timeouts
  by polling the container's cgroup (`cpu.stat`) and killing via `cgroup.kill`,
  then prints a JSON result to stdout.
- `sourcerunner/runc-base-config.json` — base OCI spec (namespaces:
  pid/network/ipc/uts/mount/cgroup).
- `language_manifest.yaml` — language name → runner image, file extension,
  optional build command, run command. Sources are written as
  `solution.<extension>` in `/solution`.
- `task_definition.yaml` — sample task: compile a C++ solution, run it on one
  input, then run a testlib checker against the output. The checker uses
  `depends_on` and both forms of `status` input.
- `cpptest/` — sample solution/input/answer used by `task_definition.yaml`.
- `static/` — judge helper programs from the legacy system (testlib checker,
  float diff grader, scorers, postprocess, validator).
- `raw_task.in` (untracked) — example of the legacy "probgate"/xjudge task JSON
  format (runs with `inmap`/`outmap`, `deps`, `arg_replacements`, scoring
  sources). Useful as a reference for features the new `JudgeTask` model does
  not yet cover.
- `make_task.py` — unfinished converter from a legacy problem `config.json` to
  a task definition. Does not run (missing `os` import, undefined `base_path`,
  `runs`, typo `souces`).
- `pipe_test.py` — ad-hoc test wiring two containers together with pipes.
- `cpu_pin_test.py` — temporary check, run inside the worker container from
  the repo root: starts one task container and verifies its affinity, cgroup
  cpuset, `cpu.max` (`100000 100000`) and actual CPU match the worker's
  single pinned CPU. Exits non-zero on mismatch.
- `bessieball/` — sample problem export from the legacy contest system:
  `config.json` (time/mem limits, `tests` as `tests/I.n`/`tests/O.n`,
  `solutions` with `type` Correct/Wrong/TLE, `lang`, `use_file_io` meaning
  `<shortname>.in`/`<shortname>.out` in the CWD), solutions, statements.
- `export_to_task.py` — temporary test helper, not meant to stay in the
  codebase. Converts an export into a JudgeTask with one run per stage
  (stage 2i: submission on test i; stage 2i+1: `static/checker.cpp` for test
  i with `depends_on`; stage 2N: `static/scorer_acwa_status.cpp` fed formatted
  `status` inputs) and, with
  `--run [--all]`, runs it through `TaskHandler` and compares the verdict to
  the solution's expected type. Only `C++` solutions are supported.
- `static/scorer_acwa_status.cpp` — status-aware replacement for
  `scorer_acwa.cpp`: reads `sol.<i>` (`{state} {exit_type} {kill_reason}`)
  and `chk.<i>` (`{state} {exit_type} {exit_status}`) and prints the first
  non-A verdict among A/W/T/R/C/E (C = build failed; W includes a missing
  output file).

## JudgeTask model summary

Task design rule: put each run in its own stage. Multiple runs in one stage
are only for interactive tasks where runs must execute concurrently and talk
over pipes. Sequential work (e.g. per-test submission then checker) uses
consecutive single-run stages linked by `depends_on` / `run` / `status`.

- `files`: name → `fs` (host path) or `inline` (string contents).
- `sources`: name → `{language, file, extra_files: {filename: file}}`.
- `stages[].runs[]`: `source`, `time_limit` (seconds, CPU), `memory_limit`
  (bytes), `output_limit` (bytes, **not enforced yet**), `args`,
  `depends_on: [{stage_index, run_index=0}]`, and:
  - `fd_map`: fd number → `read` (task file), `run` (output of an earlier
    stage's run; `ignore_if_missing`), `status` (see below), `write` (named
    output, optionally `export`ed), or `pipe` (`read`/`write`/`duplex`; must
    pair exactly two ends within one stage).
  - `files_in`: filename in `/task` → `read`, `run`, or `status`.
  - `files_out`: filename in `/task` → `write` (collected after the run).
- Run outputs are stored as `<stage_index>.<run_index>.<name>` in a per-task
  temp dir and referenced by later stages via `FileRunReadDefinition`.
- All earlier-run references (`run`, `status`, `depends_on`) must point to an
  earlier stage; checked by `_check_run_ref`.
- `status` input (`FileRunStatusDefinition`): `{stage_index, run_index=0,
  format=None}`. Without `format`: `RunStatus` JSON. With `format`: Python
  format string over flat fields (`STATUS_FORMAT_FIELDS`: state, reason,
  exit_type, exit_status, signal, error, kill_reason, time_cpu, time_sys,
  time_wall, mem_peak) plus trailing newline; `None` renders as `-`
  regardless of spec. Formats are validated (bare known names only, no
  conversions/attribute/index/nested) and test-rendered against sample values.
  Rendering: `render_run_status()`.
- `GeneratedDefinition` exists but is unused.

Naming: `FileInput` is the run-input union (read/run/status); `FileDataInput`
is the task-file union (fs/inline) used by `JudgeTask.files`.

Result models (also in `data_model.py`): `BuildStatus` (state
success/failed/error, `RuncResult`, build `log` truncated to 64 KiB),
`RunStatus` (state success/failed/skipped/error, `reason`, `RuncResult`),
`TaskResult` (`builds` by source name, `runs[stage][run]`). Success means
`exit_type == "normal"` and `exit_status == 0`.

## task_handler status / known gaps

Current behavior:
- Builds every source with a `build` command in a writable `/solution`
  scratch dir (owned by uid 1000); stdout/stderr go to a log (two distinct
  fds — the supervisor remap cannot map one fd to both 1 and 2). Failure →
  `BuildStatus(failed)`, `RuncExecutionError` → `error`; never raises.
- Per stage: pipes created up front (ends assigned per `(run_index, fd)` in
  order of appearance), then all runs via `asyncio.gather`. Each run:
  1. `_skip_reason()` (uses only earlier stages): build not successful →
     `"build failed: <source>"`; a `depends_on` run not successful →
     `"dependency stage S run R was <state>"`; a required `run` input file
     missing → `"missing input ..."`. Skipped runs close their pipe ends
     immediately and get `run_finished` without `run_started`.
  2. `_execute_run()`: materializes inputs; `ignore_if_missing` inputs are
     dropped from `/task` or given an empty pipe on their fd.
  3. Any exception → `RunStatus(error)`; pipe ends closed; stage continues.
- Reporter (`TaskReporter` Protocol): `build_started`, `build_finished`,
  `stage_started`, `run_started`, `run_finished(status, exports)`,
  `task_finished`. `exports` paths are only valid during the callback.
  Implementations: `NullReporter` (default), `JsonLinesReporter`.

Known gaps / issues:
- `write` outputs mapped in `fd_map` are created (`O_CREAT`) before the run,
  so they always exist; the missing-input skip only triggers for `files_out`
  outputs or skipped producers. Gate on success with `depends_on`.
- Sources without a `build` step (e.g. python3) never get
  `/solution/solution.<ext>` written: only built sources mount `/solution`.
- `FDContainer.socketpair()` (`runc.py`) stores `fileno()` of socket objects
  that are then garbage collected, closing the fds; duplex pipes are broken
  until it uses `detach()`.
- `write` outputs are opened without `O_TRUNC` and `output_limit` is unused.
- `files_out` handling uses synchronous `os.rename` (marked TODO).
- uid/gid 1000 and `/mnt/sourcerunner/tmp` are hardcoded.
- Likely: `/task` is created by `add_scratch("/task")` (task_handler.py,
  `_execute_run`) without an owner, so it belongs to namespace root (mode
  0755) and the run's uid 1000 cannot create files there (file-I/O
  solutions, `files_out`). Observed symptom: file-I/O submissions end
  `success` (a failed `freopen` is silent) but the checker is skipped with
  `missing input 'output' from stage N run 0 (success)`. Possible fix: pass
  `src_path_owner=(1000, 1000)`; not yet confirmed in a container.
- All runs in a stage start at once with a 1-CPU quota each and wall timeout
  `time_limit + 1`; many runs on few cores can hit `TIMEOUT_WALL`.
- No verdict/scoring layer yet; only raw run statuses.
- Not yet exercised in a real container; only a simulation with faked
  `TaskWorkspace`/`RuncRunner`/`_temp_dir` (monkeypatched on the
  `task_handler` module) has been run.

Known and intentionally deferred (leave alone unless asked): the missing
`tests/` directory and the broken `make_task.py`.

## Deployment assumptions and priorities

- Runners are expected to be pinned to a single CPU; submissions are
  CPU-bound. Parallelizing runs or stages is not a goal.
- Isolation between runs is required for fair TLE verdicts and accurate,
  comparable runtime numbers (users compete on them). Prefer designs that
  keep measured runs alone on their CPU over designs that save overhead.
- Per-run setup (workspace mounts, supervisor startup, runc create/delete)
  happens outside the timed region: CPU time comes from the container's
  cgroup, and wall time starts in `supervisor.wait_ctr` after `runc start`.
  Overhead affects throughput, not measured times. Workspace reuse was
  judged low priority; caching compiled helpers (checker/testlib, scorer)
  across tasks is the more likely throughput win (unmeasured).
- CPU pinning: `run_worker.sh` pins the whole worker container with
  `--cpuset-cpus="${SOURCERUNNER_CPU}"` (default CPU 1). `RuncRunner.create_config` sets
  only a CPU quota (100000/100000, i.e. one CPU's worth) and no cpuset, so
  task containers inherit the worker's pinning (verified in a container with
  `cpu_pin_test.py` on CPU 0 and a non-default CPU: affinity, cpuset, `cpu.max` and actual CPU all
  matched). Do not reintroduce a
  hardcoded `cpus` value (it is a cpuset of CPU ids, not a count).
- Multi-run (interactive) stages share the worker's CPU, so an interactor
  competes with the submission. Accepted trade-off: the problem-setter's
  run is expected to use minimal CPU.

## Runtime environment

Code only runs inside the source-runner container; it requires `/runners`
(overlay layers per runner), `/mnt/sourcerunner` (must be a Docker volume, not
an overlay), `runc`, `mount`/`umount`, and a delegated cgroup v2 hierarchy.

- Image is built with `tplbuild` from the Jinja-templated `Dockerfile` and
  `tplbuild.yml` (profile `default`; enabled runners: `python3`, `cpp`). Each
  runner is stored as an overlay diff (via `uniondiff`) on top of
  `/runners/lower`, with file ownership shifted by 100000.
- `run_worker.sh` starts the container (`source-runner` image) as
  `taskrun-external` with `SYS_ADMIN`, the custom `runner-seccomp.json`, and the
  repo bind-mounted at `/sourcerunner` (so local edits are live).
- `container-scripts/entrypoint.sh` → `sudo /init-root.sh` (remount cgroup2,
  enable controllers, chown `/sys/fs/cgroup/sourcerunner`) → `unshare` into a
  user+mount+cgroup namespace (container uid 1000 → ns root, 100001+ → ns
  1+) → `supervisor-init.sh` → command (default `bash`).
- Inside the container: `python3 -m sourcerunner.task_handler task_definition.yaml`
  (run from `/sourcerunner` so `language_manifest.yaml` is found).

## Development commands

```
pip install -r requirements.txt -r requirements-dev.txt
make format        # black + isort (profile=black)
make lint          # format-check + pylint + mypy on sourcerunner/
make test          # pytest -m unit tests  (no tests/ directory exists yet)
```

- Python ≥ 3.10 syntax (`match`, `X | Y`); container uses Debian trixie's
  Python, Docker diff stage uses `python:3.14`.
- `pytest-asyncio` with `asyncio_mode = "auto"`; mark fast tests `@pytest.mark.unit`.
- The `all` Makefile target references a `docs` target that does not exist.
- `make` may be unavailable; run tools directly with `.venv/bin/python -m
  black|isort|pylint|mypy`. Imports need the repo root on `PYTHONPATH`.
- Existing pylint baseline: `unused-argument` for `**context` in
  `post_validate` methods and one `f-string-without-interpolation`.

## Conventions

- Fully async code; blocking filesystem calls go through
  `loop.run_in_executor`. Resource lifetimes are managed with
  `contextlib.AsyncExitStack` / async context managers.
- Fds passed to containers must be inheritable (`FDContainer` sets this) and
  are closed in the parent after the supervisor is spawned (`close_fds`).
- Path components supplied by tasks are validated with
  `_check_simple_path_element` (no separators, not `.`/`..`/empty).
