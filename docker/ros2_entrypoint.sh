#!/bin/bash
# Sources the ROS 2 distribution and the prebuilt AirSim workspace, then hands
# over to whatever command the container was started with.
#
# nounset is deliberately not enabled: the ROS setup scripts read variables
# such as AMENT_TRACE_SETUP_FILES without setting them first, which is fine for
# bash by default but fatal under `set -u`.
set -eo pipefail

source "/opt/ros/${ROS_DISTRO}/setup.bash"
source "${AIRSIM_ROS2_WS}/install/setup.bash"

exec "$@"
