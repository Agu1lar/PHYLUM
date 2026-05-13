#!/usr/bin/env bash
set -euo pipefail
mkdir -p tests
mv test_*.py tests/ || true
echo "Moved test_*.py files to tests/"
