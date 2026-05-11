#!/usr/bin/env bash
set -euo pipefail
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m playwright install --with-deps
echo "Playwright installation complete."