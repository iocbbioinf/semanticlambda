#!/usr/bin/env bash
# Launch the KG browser with the project virtualenv
cd "$(dirname "$0")"
source .venv/bin/activate
python3 kg_browser.py "$@"
