#!/usr/bin/env bash
set -x

set -eo pipefail

id

umount /sys/fs/cgroup
mount -t cgroup2 none /sys/fs/cgroup

mkdir -p /sys/fs/cgroup/sourcerunner

xargs -rn1 < /sys/fs/cgroup/cgroup.procs > /sys/fs/cgroup/sourcerunner/cgroup.procs || :

# echo "+cpu +memory +pids" > /sys/fs/cgroup/cgroup.subtree_control
sed -e 's/ / +/g' -e 's/^/+/' < /sys/fs/cgroup/cgroup.controllers \
               > /sys/fs/cgroup/cgroup.subtree_control


chown -R taskrun-external:taskrun-external /sys/fs/cgroup/sourcerunner
