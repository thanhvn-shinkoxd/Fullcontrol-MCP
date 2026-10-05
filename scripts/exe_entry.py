"""Absolute imports let PyInstaller launch the package outside its source tree."""

from multiprocessing import freeze_support

from fullremote_mcp.__main__ import main


if __name__ == "__main__":
    freeze_support()
    main()
