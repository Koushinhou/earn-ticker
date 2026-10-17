"""Entry script used by PyInstaller and for running from source."""

import sys

from earnticker.app import main

if __name__ == "__main__":
    sys.exit(main())
