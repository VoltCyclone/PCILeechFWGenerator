#!/bin/bash
# Convenience shim around the canonical test target in the Makefile.
# Kept as a thin wrapper because downstream users and MANIFEST.in reference
# this filename. Use `make test` directly when possible.
set -e
exec make test "$@"
