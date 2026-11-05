"""Access to libs/docextract (table extraction + OCR backends).

Installed (pip install -e libs/docextract[...]) it is imported normally; in a
plain checkout the in-repo source tree is put on sys.path, so scripts and
tests work without the install step.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "libs" / "docextract" / "src"


def load():
    try:
        import docextract
    except ImportError:
        if _SRC.is_dir() and str(_SRC) not in sys.path:
            sys.path.insert(0, str(_SRC))
        import docextract
    return docextract
