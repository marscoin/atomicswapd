#!/bin/sh
# Volumes mount owned by root; hand /data to the maker user, then drop root.
set -e
if [ "$(id -u)" = "0" ]; then
  mkdir -p "${ATOMICSWAPD_DATA:-/data}"
  chown -R maker:maker "${ATOMICSWAPD_DATA:-/data}"
  exec setpriv --reuid=maker --regid=maker --init-groups "$@"
fi
exec "$@"
