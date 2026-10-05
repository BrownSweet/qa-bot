"""Tests must never load developer credentials or use the working database."""
import os
import sys
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "test-only-jwt-key-not-used-by-the-app"
os.environ["AES_KEY"] = "test-only-aes-key-not-used-by-the-app"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
