"""Entry point: ``python -m earnticker [--config-dir DIR]``."""

import sys

from earnticker.app import main

if __name__ == "__main__":
    sys.exit(main())
