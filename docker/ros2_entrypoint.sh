#!/bin/bash
# Sources the ROS 2 distribution and the prebuilt AirSim workspace, then hands
# over to whatever command the container was started with.
set -euo pipefail

source "/opt/ros/${ROS_DISTRO}/setup.bash"
source "${AIRSIM_ROS2_WS}/install/setup.bash"

exec "$@"
