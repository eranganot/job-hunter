"""
make_push_icons.py - the two pictures a push notification uses.

    pip install pillow
    python3 scripts/make_push_icons.py

web/public/noa-192.png   Noa (illustration E, the one the app uses) as a PNG: the
                         notification's large icon when the push is from Noa.
web/public/badge-96.png  The small status-bar mark on Android: the logo's suitcase,
                         white on transparent. Android paints every opaque pixel of
                         a badge one colour, so the full-colour app icon showed there
                         as a plain white square.

Build-time only; run it when the logo or Noa's picture changes, then
`python3 scripts/build_web.py` so web_bundle/ carries the new files.
"""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC = os.path.join(ROOT, "web", "public")


def noa():
    im = Image.open(os.path.join(ROOT, "assets", "landing", "noa-e.webp")).convert("RGBA")
    im = im.resize((192, 192), Image.LANCZOS)
    path = os.path.join(PUBLIC, "noa-192.png")
    im.save(path, optimize=True)
    return path


def badge():
    s = 8                                   # draw big, shrink for smooth edges
    n = 96 * s
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    white, clear = (255, 255, 255, 255), (0, 0, 0, 0)

    def box(x0, y0, x1, y1, r, fill):
        d.rounded_rectangle((x0 * s, y0 * s, x1 * s, y1 * s), r * s, fill=fill)

    box(36, 14, 60, 40, 7, white)           # handle: a rounded loop...
    box(42, 20, 54, 40, 3, clear)           # ...with its hole
    box(10, 30, 86, 80, 9, white)           # the case
    d.rectangle((10 * s, 52 * s, 86 * s, 56 * s), fill=clear)      # the seam across it
    box(42, 46, 54, 62, 3, white)           # the clasp, bridging the seam
    im = im.resize((96, 96), Image.LANCZOS)
    path = os.path.join(PUBLIC, "badge-96.png")
    im.save(path, optimize=True)
    return path


if __name__ == "__main__":
    for p in (noa(), badge()):
        print("wrote %s (%d bytes)" % (p, os.path.getsize(p)))
