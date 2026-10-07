"""
IR Chatbot backend (Python / FastAPI) — wraps 3DEXPERIENCE DSX REST APIs.

Run:
  pip install -r requirements.txt
  uvicorn server:app --reload --port 3001

Implementation lives under backend/. The previous monolith is kept as server_legacy.py.
"""

from backend.app_full import app

__all__ = ["app"]
