@echo off
rem Runs the tracker on this computer. Open http://localhost:8501 here, or from your phone with Tailscale (see README).
rem Prices are kept 60 days here instead of 5 because this computer stays on.
if not exist .venv ( python -m venv .venv )
call .venv\Scripts\activate
pip install -q -r requirements.txt
set RETENTION_DAYS=60
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
