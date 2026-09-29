"""Test setup: headless Qt + an isolated HOME so tests never touch real settings/history."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP_HOME = tempfile.mkdtemp(prefix="groqchat_test_home_")
os.environ["HOME"] = _TMP_HOME
os.environ["USERPROFILE"] = _TMP_HOME  # Windows
for var in ("GROQ_API_KEY", "OPENROUTER_API_KEY"):
    os.environ.pop(var, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
