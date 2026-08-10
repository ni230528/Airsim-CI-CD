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
to any apt repository, so the job fetches and builds it from source.

The reference is pinned to the commit
`b9bf3f9f76a6a5004880e2894fe94d1e43837e40` (2025-09-24) rather than to a
branch, because **no released `px4_msgs` branch satisfies the layouts that
`airsim_px4_offboard/px4_schema.py` validates against**:

| Message | `px4_schema.py` requires | `release/1.16` | `main` | pinned commit |
| --- | --- | --- | --- | --- |
| `SensorGps` | `authentication_state`, `system_error` | absent | present | present |
| `VehicleStatus` | `MESSAGE_VERSION` 1 | 1 | 4 | 1 |
| `VehicleAttitude` | `MESSAGE_VERSION` 0 | 0 | - | 0 |
| `VehicleOdometry` | `MESSAGE_VERSION` 0 | 0 | - | 0 |
| `VehicleRatesSetpoint` | `MESSAGE_VERSION` 0 | 0 | - | 0 |

`release/1.16` fails the `SensorGps` contract and `main` fails the
`VehicleStatus` one, so the supported revision is a point on `main` after
`SensorGps` gained those fields and before `VehicleStatus` was versioned past
1. The documentation elsewhere in this repository describes the supported set
as "PX4 v1.16.x", which is approximately but not exactly true.

Because the pin is a commit rather than a branch, the job cannot use
`git clone --branch`; it initialises the repository and fetches the single
commit by sha instead. A manual `workflow_dispatch` run accepts a different
`px4_msgs_ref` input, which may be a commit, branch or tag.

Pinning a commit also keeps the pipeline reproducible. A branch is a moving
target: a build that passes today can fail tomorrow with no change on this
side.

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

### Build caching

Building the workspace from scratch takes around six minutes, roughly 87% of
the run, and almost all of it is `rosidl` generating code for the several
hundred `px4_msgs` message types. Those messages are pinned to a fixed commit
and never change between runs, so `ros2/build` and `ros2/install` are cached.

The cache key is composed of everything that can legitimately invalidate the
tree:

| Key component | Invalidates when |
| --- | --- |
| `runner.os` | the runner platform changes |
| `ROS_DISTRO` | the distribution changes |
| `ros-<distro>-ros-base` package version | the container's ROS packages are updated upstream |
| `PX4_MSGS_REF` | the pinned `px4_msgs` commit is changed |
| hash of the two package source trees | any source file changes |

The container image tag `ros:humble-ros-base` is mutable, so the base package
version is read at runtime with `dpkg-query` and folded into the key. Without
it a cached tree could outlive the toolchain it was built with.

`restore-keys` provides the partial-hit fallback that produces most of the
saving: when only the AirSim sources change, the exact key misses but the
prefix key still matches, the `px4_msgs` build is restored, and colcon rebuilds
only the two small packages.

The trade-off is real. A restored build tree can let a run pass where a clean
build would fail, because a stale artifact satisfies something that no longer
builds. The `clean_build` input on `workflow_dispatch` exists for that: it
skips the cache entirely, which is worth running before cutting a release and
whenever a result looks suspicious.

That input is compared as a string in a separate step rather than tested
directly with `if: ${{ !inputs.clean_build }}`. A `workflow_dispatch` input
declared as `type: boolean` arrives in the expression context as the string
`"false"`, and every non-empty string is truthy, so the negation was false and
the cache step was silently skipped on every manually triggered run. Nothing
failed; the build was simply six minutes slower with no indication why. The
step now logs which branch it took, so the same mistake cannot hide again.

### Tests

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
| `docs_site` | the rendered documentation, as a Pages artifact |
| `deploy_docs` | publishes that artifact to GitHub Pages |
| `ros2_image` | container image published to GitHub Container Registry |
| `promote_field` | retags the verified image as `:field` behind an approval gate |
| `publish_release` | the GitHub Release itself |

`deploy_docs` requires Pages to be enabled for the repository with its source
set to GitHub Actions, under Settings -> Pages. The job deploys into the
`github-pages` environment, which GitHub manages itself.

Documentation is deployed rather than attached to the release. An earlier
version shipped a `docs-site.tar.gz` asset, which came to 127 MB because the
`docs` directory is mostly images; every release would have carried that
weight, and a tarball is a poor way to read documentation compared with a URL.

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

The job deliberately does not check out the repository, because it only needs
the artifacts. `gh` normally identifies the target repository by reading the
git remotes of the working directory, which does not exist here, so `GH_REPO`
is set explicitly instead. Without it the release step fails even though the
token and permissions are correct.

A tag whose name contains a hyphen is published with `--prerelease`, following
the semver rule that anything after the hyphen is a pre-release identifier.
GitHub does not infer this: without the flag, `v0.0.1-rc2` was published as a
full release and labelled Latest, so anything reading `releases/latest` would
have been handed a release candidate.

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
