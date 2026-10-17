"""Generate assets/earnticker.ico (gold coin) using Qt only.

The .ico is written by hand as PNG-compressed entries so no extra imaging
library (or Qt image plugin) is needed.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from earnticker.ui.common import coin_pixmap  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(size: int) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    coin_pixmap(size).toImage().save(buf, "PNG")
    buf.close()
    return bytes(data.data())


def main() -> int:
    QGuiApplication(sys.argv[:1])
    images = [(s, png_bytes(s)) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    blobs = b""
    for size, blob in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        blobs += blob
    out = ROOT / "assets" / "earnticker.ico"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(header + entries + blobs)
    (ROOT / "assets" / "earnticker.png").write_bytes(images[-1][1])
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
