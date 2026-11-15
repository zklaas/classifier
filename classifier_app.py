#!/usr/bin/env python3
"""Classifier: Development Groups, clearance and licences for your software.
A Linux tool for developers.

    classifier                  the window
    classifier list|scan|show|licence|github|review ...   from a terminal
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    if not sys.platform.startswith("linux"):
        sys.exit("Classifier is a Linux tool for developers and runs on Linux only.\n"
                 "(The software it looks after can be for any operating system.)")
    if len(sys.argv) > 1:
        from classifier import cli
        cli.main(sys.argv[1:])
    else:
        from classifier import gui
        sys.exit(gui.run(sys.argv))
