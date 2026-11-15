"""
make_share_image.py - draws assets/landing/share.png, the 1200x630 picture a
link to the site shows on Facebook, LinkedIn, WhatsApp and X (og:image).

Run it only when the landing headline, the colours or Noa's picture change:

    pip install pillow fonttools brotli
    python3 scripts/make_share_image.py

It is a build-time tool: the app itself never imports Pillow. The picture uses
the landing page's own fonts, colours, Noa illustration and demo job card, so
it makes no claim the page does not make.
"""
import io
import os

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANDING = os.path.join(ROOT, "assets", "landing")
OUT = os.path.join(LANDING, "share.png")

W, H = 1200, 630
BG, PANEL, LINE = "#0c1f17", "#12291f", "#2c4a3c"
STRONG, TEXT, MUTED, ACCENT, ON = "#fbf6e6", "#c4d4c6", "#8ba593", "#ffd766", "#1f1a05"

HEADLINE = ["Meet Noa, your", "personal AI", "headhunter agent."]
SUB = ["She works for you,", "not for the companies."]
OFFER = "Premium is free for a limited time"
BUBBLE = ["Send me your CV.", "Then I do the searching."]


def font(name, size, weight):
    """A Pillow font from the page's own woff2 at one weight."""
    f = TTFont(os.path.join(LANDING, name + "-latin-wght.woff2"))
    f.flavor = None
    buf = io.BytesIO()
    f.save(buf)
    buf.seek(0)
    ft = ImageFont.truetype(buf, size)
    ft.set_variation_by_axes([weight])
    return ft


def rounded(img, radius):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.size[0], img.size[1]), radius, fill=255)
    img.putalpha(mask)
    return img


def main():
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    # brand
    icon = Image.open(os.path.join(ROOT, "web", "public", "icon-192.png")).convert("RGBA").resize((52, 52), Image.LANCZOS)
    im.paste(icon, (64, 56), icon)
    d.text((130, 64), "Job Hunter", font=font("inter", 30, 700), fill=STRONG)

    # headline + sub
    y = 160
    hf = font("fraunces", 58, 600)
    for line in HEADLINE:
        d.text((64, y), line, font=hf, fill=STRONG)
        y += 66
    y += 22
    sf = font("inter", 30, 400)
    for line in SUB:
        d.text((64, y), line, font=sf, fill=TEXT)
        y += 42

    # offer pill
    of = font("inter", 24, 700)
    tw = d.textlength(OFFER, font=of)
    d.rounded_rectangle((64, 530, 64 + tw + 48, 582), 26, fill=ACCENT)
    d.text((88, 541), OFFER, font=of, fill=ON)

    # the demo job card, cropped to its top
    card = Image.open(os.path.join(LANDING, "job-card.webp")).convert("RGBA")
    cw = 480
    card = card.resize((cw, int(card.size[1] * cw / card.size[0])), Image.LANCZOS).crop((0, 0, cw, 330))
    card = rounded(card, 22)
    cx, cy = 660, 250
    d.rounded_rectangle((cx - 2, cy - 2, cx + cw + 2, cy + 332), 24, fill=LINE)
    im.paste(card, (cx, cy), card)

    # Noa and her message
    noa = Image.open(os.path.join(LANDING, "noa-e.webp")).convert("RGBA").resize((112, 112), Image.LANCZOS)
    mask = Image.new("L", (112, 112), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 112, 112), fill=255)
    nx, ny = 660, 70
    d.ellipse((nx - 4, ny - 4, nx + 116, ny + 116), fill=ACCENT)
    im.paste(noa, (nx, ny), mask)
    bx = nx + 136
    d.rounded_rectangle((bx, ny, 1140, ny + 132), 26, fill=PANEL, outline=LINE, width=2)
    d.rectangle((bx, ny, bx + 26, ny + 26), fill=PANEL)       # square corner by Noa, like the page's bubbles
    d.line((bx, ny, bx + 26, ny), fill=LINE, width=2)
    d.line((bx, ny, bx, ny + 26), fill=LINE, width=2)
    d.text((bx + 24, ny + 16), "Noa", font=font("inter", 18, 500), fill=MUTED)
    bf = font("inter", 25, 500)
    for i, line in enumerate(BUBBLE):
        d.text((bx + 24, ny + 46 + i * 36), line, font=bf, fill=STRONG)

    im.save(OUT, optimize=True)
    print("wrote %s (%d bytes)" % (OUT, os.path.getsize(OUT)))


if __name__ == "__main__":
    main()
