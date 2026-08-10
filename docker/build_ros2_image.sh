#!/bin/bash
# Builds and smoke-tests the same ROS 2 image the release pipeline builds, so
# it can be verified locally before a tag is pushed.
#
# Overridable: ROS_DISTRO, PX4_MSGS_REF, IMAGE
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "${SCRIPT_DIR}")"

ROS_DISTRO="${ROS_DISTRO:-humble}"
PX4_MSGS_REF="${PX4_MSGS_REF:-release/1.16}"
IMAGE="${IMAGE:-airsim-ros2:local}"

echo "Building ${IMAGE} (ROS ${ROS_DISTRO}, px4_msgs ${PX4_MSGS_REF})"

docker build \
	--file "${REPO_DIR}/docker/Dockerfile_ros2" \
	--build-arg ROS_DISTRO="${ROS_DISTRO}" \
	--build-arg PX4_MSGS_REF="${PX4_MSGS_REF}" \
	--tag "${IMAGE}" \
	"${REPO_DIR}"

echo "Checking the workspace packages are present"
docker run --rm "${IMAGE}" ros2 pkg list | grep -E '^(airsim_interfaces|airsim_px4_offboard|px4_msgs)$'

echo "Running the package test suite inside the image"
docker run --rm --workdir "/opt/airsim_ros2_ws/src/airsim_px4_offboard" "${IMAGE}" \
	python3 -m pytest test -q

echo "OK: ${IMAGE}"
