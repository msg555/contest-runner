#!/usr/bin/env bash

cd "$(dirname "${BASH_SOURCE[0]}")"

  #--security-opt seccomp="${PWD}/runner-seccomp.json" \
# TODO: Can we get an appropriate apparmor profile?
docker run \
  --privileged \
  --security-opt seccomp=unconfined \
  --security-opt apparmor=unconfined \
  --volume=source-runner-data:/runnerdata \
  -it --rm -v "${PWD}:/sourcerunner" \
  source-runner # bash


  #--entrypoint /bin/bash \
# Note, /runnerdata must be a volume/cannot be part of a container mount
