#!/usr/bin/env bash

set -eo pipefail

mount --type cgroup2 none /sys/fs/cgroup

mkdir /sys/fs/cgroup/supervisor
xargs -rn1 < /sys/fs/cgroup/cgroup.procs > /sys/fs/cgroup/supervisor/cgroup.procs || :

exec "$@"
