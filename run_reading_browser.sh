#!/usr/bin/env bash
# Launch the reading browser with the project virtualenv
cd "$(dirname "$0")"
source .venv/bin/activate
python3 reading_browser.py "$@"
