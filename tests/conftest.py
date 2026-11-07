import sys
from pathlib import Path

# The dashboard modules live in app/ (Streamlit runs them from that folder)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
