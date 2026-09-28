#!/usr/bin/env bash

set -eo pipefail

sudo /init-root.sh

CMD=("${@}")
if [ "${#CMD[@]}" -eq 0 ]; then
  CMD=(bash)
fi

exec unshare --fork --user \
  --map-users 0:1000:1 --map-users 1:100001:65535 \
  --map-groups 0:1000:1 --map-groups 1:100001:65535 \
  --mount --propagation unchanged \
  --cgroup \
  -- /supervisor-init.sh "${CMD[@]}"
