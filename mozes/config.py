import os
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = Path(os.environ.get("MOZES_DB_PATH", "mozes.db"))
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "")
TIINGO_API_KEY = os.environ.get("TIINGO_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MOZES_AI_MODEL = os.environ.get("MOZES_AI_MODEL", "")
