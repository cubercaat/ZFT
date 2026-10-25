#!/usr/bin/env bash
# Eski usul (papkadan o'rnatish). Endi tavsiya etilgani — GitHub'dan bitta buyruq: README, "O'rnatish".
set -euo pipefail
exec bash "$(dirname "$0")/setup.sh" "$@"
