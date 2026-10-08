"""Fail early with a plain-English fix instead of a traceback on common setup problems."""
import sys

FIX = ("  cd to the project folder, then:\n"
       "    python3 -m venv .venv\n"
       "    source .venv/bin/activate        (Windows: .venv\\Scripts\\activate)\n"
       "    pip install -r requirements.txt\n")


def check() -> None:
    if sys.version_info < (3, 9):
        sys.exit(f"Python 3.9+ is required (this is {sys.version.split()[0]}).")
    missing = []
    for mod in ("pandas", "numpy", "requests", "yaml", "dotenv", "feedparser", "xlrd"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        sys.exit(f"Missing packages: {', '.join(missing)}\n"
                 f"(running {sys.executable} - is the virtualenv active?)\n{FIX}")
    import pandas as pd
    if tuple(int(x) for x in pd.__version__.split(".")[:2]) < (2, 2):
        sys.exit(f"pandas {pd.__version__} is too old (need 2.2+). Run: pip install -U -r requirements.txt")
