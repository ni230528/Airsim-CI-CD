#!/usr/bin/env python3
"""Validate the GitHub Actions workflow files beyond generic YAML parsing.

A workflow file can be perfectly valid YAML and still be a broken workflow:
a job with no steps, a step that is neither `run` nor `uses`, a trigger block
that got lost in a bad merge. GitHub only reports those once the workflow has
been pushed, so this check runs locally and in CI instead.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


WORKFLOW_DIR = Path(".github/workflows")

# YAML 1.1 resolves the bare word `on` to the boolean True, so PyYAML turns
# the workflow trigger block into the key True rather than the string "on".
# Both spellings are accepted here: `on:` and the quoted `"on":`.
ON_KEYS = (True, "on")


def load_workflow(path: Path) -> tuple[Any, list[str]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle), []
    except yaml.YAMLError as error:
        return None, [f"{path}: is not valid YAML ({error.__class__.__name__})"]


def validate_step(step: Any, where: str) -> list[str]:
    if not isinstance(step, dict):
        return [f"{where}: expected a mapping, got {type(step).__name__}"]

    has_run = "run" in step
    has_uses = "uses" in step

    if has_run and has_uses:
        return [f"{where}: declares both `run` and `uses`; a step must do exactly one"]
    if not has_run and not has_uses:
        return [f"{where}: declares neither `run` nor `uses`"]
    if has_run and not isinstance(step["run"], str):
        return [f"{where}: `run` must be a string, got {type(step['run']).__name__}"]

    return []


def validate_job(name: str, job: Any, where: str) -> list[str]:
    if not isinstance(job, dict):
        return [f"{where}: expected a mapping, got {type(job).__name__}"]

    # A job either runs steps on a runner, or delegates to a reusable workflow.
    if "uses" in job:
        return []

    failures: list[str] = []

    if "runs-on" not in job:
        failures.append(f"{where}: has no `runs-on`")

    steps = job.get("steps")
    if not isinstance(steps, list) or not steps:
        failures.append(f"{where}: has no steps")
        return failures

    for index, step in enumerate(steps):
        failures.extend(validate_step(step, f"{where}.steps[{index}]"))

    return failures


def validate_workflow(path: Path, data: Any) -> list[str]:
    if not isinstance(data, dict):
        return [f"{path}: expected a mapping at the document root"]

    failures: list[str] = []

    if not any(key in data for key in ON_KEYS):
        failures.append(f"{path}: has no `on` trigger block")

    if "name" not in data:
        failures.append(f"{path}: has no `name`")

    jobs = data.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        failures.append(f"{path}: has no jobs")
        return failures

    for job_name, job in jobs.items():
        failures.extend(validate_job(job_name, job, f"{path}:{job_name}"))

    return failures


def main() -> int:
    workflows = sorted(
        set(WORKFLOW_DIR.glob("*.yml")) | set(WORKFLOW_DIR.glob("*.yaml"))
    )

    if not workflows:
        print(f"No workflow files found under {WORKFLOW_DIR}", file=sys.stderr)
        return 1

    failures: list[str] = []

    for workflow in workflows:
        data, load_failures = load_workflow(workflow)
        if load_failures:
            failures.extend(load_failures)
            continue

        workflow_failures = validate_workflow(workflow, data)
        failures.extend(workflow_failures)

        status = "FAILED" if workflow_failures else "ok"
        print(f"{status}: {workflow}")

    if failures:
        print("Workflow validation failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print(f"Validated {len(workflows)} workflow file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
