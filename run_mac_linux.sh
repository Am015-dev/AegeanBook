#!/bin/sh
# Runs the tracker on this computer. Open http://localhost:8501 here, or from your phone with Tailscale (see README).
# Prices are kept 60 days here instead of 5 because this computer stays on.
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
RETENTION_DAYS=60 exec streamlit run app.py --server.address 0.0.0.0 --server.port 8501
