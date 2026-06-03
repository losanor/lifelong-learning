from pathlib import Path
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

load_dotenv(dotenv_path=ENV_PATH)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

USE_MOCK_MODEL = os.getenv("USE_MOCK_MODEL", "false").lower() == "true"

DEFAULT_EXECUTOR = os.getenv("DEFAULT_EXECUTOR", "claude_code")
CODEX_COST_PER_TOKEN = float(os.getenv("CODEX_COST_PER_TOKEN", "0.0000015"))
EXECUTOR_MAX_ITERATIONS = int(os.getenv("EXECUTOR_MAX_ITERATIONS", "5"))
EXECUTOR_MAX_DURATION_SECONDS = int(os.getenv("EXECUTOR_MAX_DURATION_SECONDS", "300"))