"""
Preview launcher: patches os.getcwd() so Streamlit can start in a sandboxed env.
"""
import os
import sys

PROJECT_DIR = "/Users/songbin/Documents/GitHub/WealthPilot"
os.chdir(PROJECT_DIR)
os.getcwd = lambda: PROJECT_DIR  # patch before streamlit imports

port = os.environ.get("PORT", "8501")
sys.argv = ["streamlit", "run", "streamlit_app.py", f"--server.port={port}", "--server.headless=true"]

from streamlit.web.cli import main
main()
