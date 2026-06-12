"""
config.py
---------
Single source of truth for all paths in Veris.

Railway: set DATA_DIR=/data in environment (Volume mounted at /data)
Render:  set DATA_DIR=/data in environment (Disk mounted at /data)
Local:   DATA_DIR defaults to the project directory

All modules import DB_FILE, MEMOS_DIR, WATCHLIST_FILE from here.
Never hardcode paths in other files.
"""

import os
from pathlib import Path

# Code always lives here (never changes)
CODE_DIR = Path(__file__).parent

# Data (database + memos) goes to persistent volume on cloud, local dir otherwise
DATA_DIR = Path(os.environ.get("DATA_DIR", CODE_DIR))

# Ensure data directory exists on startup
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "memos").mkdir(parents=True, exist_ok=True)

DB_FILE        = str(DATA_DIR / "database.db")
MEMOS_DIR      = str(DATA_DIR / "memos")
WATCHLIST_FILE = str(CODE_DIR / "watchlist.json")  # always from code dir
