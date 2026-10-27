import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

MODEL_API_KEY = os.getenv("MODEL_API_KEY", "")
META_BASE_URL = os.getenv("META_BASE_URL", "https://api.meta.ai/v1")
AGENT_MODEL = os.getenv("AGENT_MODEL", "muse-spark-1.3")
TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "muse-voice-transcribe-1.0")
DATA_DIR = ROOT / "data"
# Reports on the same feeder within this window are treated as one fault
CLUSTER_WINDOW_MIN = int(os.getenv("CLUSTER_WINDOW_MIN", "120"))
