#!/usr/bin/env bash

cd "$(dirname "${BASH_SOURCE[0]}")"

# CPU the whole worker container (and every task container it starts) is
# pinned to, for fair and consistent timing.
SOURCERUNNER_CPU="${SOURCERUNNER_CPU:-1}"

  #--security-opt seccomp="${PWD}/runner-seccomp.json" \
# TODO: Can we get an appropriate apparmor profile?

docker run \
  --user=taskrun-external \
  --cap-add SYS_ADMIN \
  --cpuset-cpus="${SOURCERUNNER_CPU}" \
  --security-opt seccomp=./runner-seccomp.json \
  --security-opt apparmor=nautilus \
  --security-opt systempaths=unconfined \
  -v ./container-scripts/entrypoint.sh:/entrypoint.sh \
  -v ./container-scripts/init-root.sh:/init-root.sh \
  -v ./container-scripts/supervisor-init.sh:/supervisor-init.sh \
  --volume=source-runner-data:/mnt/sourcerunner \
  -it --rm -v "${PWD}:/sourcerunner" \
  source-runner # bash


exit 0

  --security-opt seccomp=unconfined \
  #--cap-add SYS_ADMIN \
  #--cap-add SYS_ADMIN \
  --cap-add=SYS_PTRACE \
  --cap-add CAP_SETUID \
  --cap-add CAP_SETGID \

# Note, /runnerdata must be a volume/cannot be part of a container mount
