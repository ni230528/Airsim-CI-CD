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

`rosdep install` runs with `--skip-keys ament_python`. The
`airsim_px4_offboard` manifest declares `ament_python` as a `buildtool_depend`,
but `ament_python` is a colcon build type rather than a released package, so no
rosdep key of that name exists and resolution fails without the skip. Removing
that line from `package.xml` would be the deeper fix; the export block already
declares the build type correctly.

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

`release.yml` is triggered by pushing a `v*` tag. A tag rather than a branch is
the trigger because a tag names exactly one commit, so every published artifact
is traceable back to a single source state.

It has five jobs. The three build jobs run in parallel; the two publishing jobs
wait on them through `needs:`.

| Job | Produces |
| --- | --- |
| `python_client` | sdist and wheel from `PythonClient` |
| `docs_site` | `docs-site.tar.gz` of the rendered documentation |
| `ros2_image` | container image published to GitHub Container Registry |
| `promote_field` | retags the verified image as `:field` behind an approval gate |
| `publish_release` | the GitHub Release itself |

### ros2_image

The primary deliverable. `docker/Dockerfile_ros2` builds the ROS 2 workspace -
`airsim_interfaces`, `px4_msgs` and `airsim_px4_offboard` - into an image that
carries a prebuilt `install/` tree and an entrypoint that sources it. Anything
that can run a container then has the exact environment CI validated, with no
`colcon build` on the target machine.

Before the image is pushed it is started twice: once to assert the three
packages appear in `ros2 pkg list`, and once to run the package test suite
inside the image. An image that builds but cannot run its own tests is not a
publishable result, and neither check requires the registry, so a failure never
leaves a broken tag behind.

Images are tagged with the release version and with `sim`. The image name is
derived as `ghcr.io/<repository>/ros2` and lowercased in the workflow, because
GHCR rejects references containing uppercase characters while a GitHub
repository name may contain them.

The registry login uses the automatic `GITHUB_TOKEN` with `packages: write`
granted to that single job. No personal access token is involved.

Note that GHCR packages are private on first publish even when the repository
is public. Make the package public under its package settings if the image is
meant to be pullable anonymously.

### promote_field

A deliberate model of staged rollout. `ros2_image` publishes the version tag
and `sim` automatically, but moving the same digest to `:field` runs in the
`field-drones` environment, so a required reviewer configured on that
environment must approve before a build reaches hardware. Nothing is rebuilt:
`docker buildx imagetools create` retags the digest that was already tested.

Until the environment is created with protection rules under Settings ->
Environments, the job runs without waiting. Adding the reviewer is what turns
this from Continuous Deployment into Continuous Delivery.

### publish_release

Downloads the artifacts the build jobs produced rather than rebuilding them, so
what is published is what was tested. It creates the Release through the
preinstalled `gh` CLI with the automatic token instead of a third-party action,
and prepends the `docker pull` reference for the image to the auto-generated
notes.

`permissions: contents: write` is scoped to this one job. Every other job in
the workflow, and all of CI, stays read-only.

A `workflow_dispatch` run performs every build and verification step but skips
`promote_field` and `publish_release`, so the pipeline can be exercised end to
end without publishing a release.

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

The release image is built and smoke-tested locally with the same arguments the
pipeline uses:

```bash
./docker/build_ros2_image.sh
```

`ROS_DISTRO`, `PX4_MSGS_REF` and `IMAGE` are overridable as environment
variables. A repository-root `.dockerignore` keeps the build context to the few
megabytes the image actually needs instead of the full checkout, which is
dominated by `Unreal` and `docs`.

## Pinned versions

`tools/ci/requirements.txt` pins the Python tooling used by CI. Bump those
pins in their own commit so that a red pipeline is never ambiguous between a
regression in this repository and a dependency that released the same day.
