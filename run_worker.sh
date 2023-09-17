#!/usr/bin/env bash

cd "$(dirname "${BASH_SOURCE[0]}")"

  #--security-opt seccomp="${PWD}/runner-seccomp.json" \
# TODO: Can we get an appropriate apparmor profile?
docker run \
  --cap-add CAP_CHOWN \
  --security-opt seccomp=unconfined \
  --security-opt apparmor=unconfined \
  --volume=source-runner-data:/runnerdata \
  -it --rm -v "${PWD}:/sourcerunner" \
  --entrypoint /bin/bash \
  source-runner # bash
