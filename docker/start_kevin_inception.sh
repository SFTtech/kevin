#!/bin/bash
docker run --privileged \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /sys/fs/cgroup:/sys/fs/cgroup:ro \
  --tmpfs /run \
  --name kevin-inception \
  --rm -dt \
  kevin-inception
