#!/usr/bin/env python3
"""Launch the PasteMD Linux desktop application."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pastemd.linux.gui import main

if __name__ == '__main__':
    sys.exit(main())
