# CI/CD

Automation for this repository is GitHub Actions only. Two workflows live in
`.github/workflows`:

| Workflow | File | Runs on |
| --- | --- | --- |
| CI | `ci.yml` | every pull request, every push to `main`, manual dispatch |
| Release | `release.yml` | every `v*` tag, manual dispatch |

Everything runs on GitHub-hosted runners. No self-hosted machine, no GPU and
no Unreal Engine installation is required to get a green pipeline.

## CI

`ci.yml` defines three independent jobs. None of them declares `needs:`, so
all three start at once and the total wall-clock time is the slowest job
rather than the sum of all of them.

### static_checks

Seconds-long checks that fail fast on mistakes which never need a compiler:

- `tools/ci/check_repo_text.py` rejects unresolved merge conflict markers and
  files that mix LF and CRLF internally. The repository intentionally keeps
  both endings across different files through `.gitattributes`, so only
  mixing *inside a single file* is an error. Five legacy `LogViewer` files are
  explicitly exempt.
- `tools/ci/validate_ci_yaml.py` parses every workflow file and asserts the
  structure GitHub expects: a `name`, an `on` trigger block, at least one job,
  a `runs-on` per job, at least one step per job, and exactly one of `run` or
  `uses` per step.
- `python -m compileall` byte-compiles `PythonClient`, `tools/ci` and
  `ros2/src`, which catches syntax errors without importing anything.
- `mkdocs build --strict` renders this documentation and fails on broken
  internal links.

### python_client

Builds the sdist and wheel from `PythonClient/pyproject.toml`, then installs
the wheel into a clean virtualenv and imports `cosysairsim` from it. A wheel
that builds but cannot be installed or imported is not a passing result. The
distribution is uploaded as the `python-client-dist` artifact.

### ros2_humble

Runs inside the official `ros:humble-ros-base` container so the ROS
environment is pinned rather than inherited from whichever tools the GitHub
runner image happens to ship.

`px4_msgs` is generated from the PX4 firmware source tree and is not published
to any apt repository, so the job clones and builds it from source. The
message layouts that `airsim_px4_offboard/px4_schema.py` validates against
match PX4 v1.16, so the default reference is `release/1.16`. A manual
`workflow_dispatch` run accepts a different `px4_msgs_ref` input when testing
against another firmware revision.

The job builds `--packages-up-to airsim_px4_offboard px4_msgs`, which covers
`airsim_interfaces`, `px4_msgs` and `airsim_px4_offboard`. It deliberately
does not build `airsim_ros_pkgs`: that package is C++, depends on PCL and
MAVROS, and needs a compiled AirLib, which belongs in a heavier job.

Tests then run with `colcon test`, followed by `colcon test-result --verbose`.
The second command is not redundant. `colcon test` exits 0 even when
individual test cases fail; `colcon test-result` is what turns a failing test
into a red pipeline. Test reports are uploaded as `ros2-test-results` whether
the job passed or failed.

## Release

`release.yml` is triggered by pushing a `v*` tag. It builds the Python client
distribution and a `docs-site.tar.gz` archive of the rendered documentation,
uploads both as workflow artifacts, and then creates a GitHub Release with
generated notes and both files attached.

Release creation uses the preinstalled `gh` CLI with the automatic
`GITHUB_TOKEN` rather than a third-party action, which keeps the workflow free
of external supply-chain trust. `permissions: contents: write` is scoped to
this workflow only; CI stays read-only.

A `workflow_dispatch` run performs every packaging step but skips release
creation, so the artifacts can be inspected without publishing anything.

## What is deliberately not automated

- **Unreal plugin packaging.** `RunUAT BuildPlugin` needs a Windows machine
  with Unreal Engine 5.5 and Visual Studio 2022. That requires a self-hosted
  runner and is done manually, see
  [Install from Source on Windows](install_windows.md).
- **AirLib C++ builds.** A full `build.sh` run costs roughly twenty minutes of
  runner time per push. Add a job for it if the C++ sources start changing
  regularly.
- **Simulation-in-the-loop tests.** Anything that actually launches AirSim
  needs a GPU, which GitHub-hosted runners do not provide.

## Running the same checks locally

Before pushing a change to CI, run what the `static_checks` job runs:

```bash
python -m pip install -r tools/ci/requirements.txt
python tools/ci/check_repo_text.py
python tools/ci/validate_ci_yaml.py
python -m compileall -q PythonClient tools/ci ros2/src
mkdocs build --strict
```

The ROS 2 job is reproducible locally with the same container the pipeline
uses:

```bash
docker run --rm -it -v "$PWD:/repo" -w /repo ros:humble-ros-base bash
```

Then follow the same steps as the `ros2_humble` job: clone `px4_msgs` into
`ros2/src`, run `rosdep install`, `colcon build` and `colcon test`.

## Pinned versions

`tools/ci/requirements.txt` pins the Python tooling used by CI. Bump those
pins in their own commit so that a red pipeline is never ambiguous between a
regression in this repository and a dependency that released the same day.
