"""
Harbour brand builder: cuts every app icon and banner from the source art in branding/source/.

    pip install pillow && python branding/make_brand.py

Sources (keep these, everything else is generated):
  source/icon-frame.jpg   bridge-and-play mark inside a glowing rounded frame (square icon source)
  source/banner-16x9.jpg  framed 16:9 banner (Fire TV launcher banner)
  source/banner-wide.jpg  wide banner, mark on the left (README / social header)
  source/portrait.jpg     tall poster (iPhone launch screen / wallpaper)

Outputs:
  harbour-icon-1024.png         full-bleed square, opaque (iOS / App Store style, iOS rounds it itself)
  harbour-icon-rounded-1024.png same, rounded corners on transparent (Android legacy icon look)
  harbour-banner-1280.png       1280x720 banner
  harbour-header-wide.png       wide header for the README
  android*/res/...              ic_launcher (mdpi..xxxhdpi), logo.png, banner.png
  web/                          iPhone web app icons: apple-touch-icon, PWA 192/512, maskable, favicons,
                                launch image
"""
import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "source")
ROOT = os.path.join(HERE, "..")
ANDROID_RES = [os.path.join(ROOT, d, "app", "src", "main", "res") for d in ("android", "android-v2")]
WEB = os.path.join(HERE, "web")

# Glowing frame in icon-frame.jpg (2576x1438): outer line sits at x 738..1837, y 169..1267.
FRAME_CX, FRAME_CY = 1288, 718
INSIDE = 1050        # square cut, just inside the frame line
INNER_RADIUS = 175   # corner radius of the area kept from the source
NAVY_TOP, NAVY_BOT = (24, 36, 52), (9, 12, 28)
MARK_SCALE = 0.84  # mark size within the square icon


def rounded_mask(size, radius, feather=0):
    s = 4  # supersample for smooth edges
    m = Image.new("L", (size * s, size * s), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size * s - 1, size * s - 1), radius * s, fill=255)
    m = m.resize((size, size), Image.LANCZOS)
    return m.filter(ImageFilter.GaussianBlur(feather)) if feather else m


def gradient(size, top, bot):
    g = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        g.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bot)))
    return g.resize((size, size))


def square_icon():
    """Full-bleed square: the inside of the frame, with the frame's corner arcs painted out."""
    src = Image.open(os.path.join(SRC, "icon-frame.jpg")).convert("RGB")
    h = INSIDE // 2
    cut = src.crop((FRAME_CX - h, FRAME_CY - h, FRAME_CX + h, FRAME_CY + h))
    # Inset the keep-mask a little so no trace of the glowing line survives at the corners.
    inset = 28
    keep = Image.new("L", cut.size, 0)
    keep.paste(rounded_mask(INSIDE - 2 * inset, INNER_RADIUS, feather=10), (inset, inset))
    # Edges (not corners) are clean interior, so keep the full straight runs.
    edge = Image.new("L", cut.size, 0)
    d = ImageDraw.Draw(edge)
    d.rectangle((INNER_RADIUS + inset, 0, INSIDE - INNER_RADIUS - inset, INSIDE), fill=255)
    d.rectangle((0, INNER_RADIUS + inset, INSIDE, INSIDE - INNER_RADIUS - inset), fill=255)
    edge = edge.filter(ImageFilter.GaussianBlur(30))
    keep = Image.composite(Image.new("L", cut.size, 255), keep, edge)
    out = Image.composite(cut, gradient(INSIDE, NAVY_TOP, NAVY_BOT), keep)
    # Shrink onto a matching navy canvas so the mark has breathing room inside the rounded corners.
    inner = round(1024 * MARK_SCALE)
    out = out.resize((inner, inner), Image.LANCZOS)
    canvas = gradient(1024, NAVY_TOP, NAVY_BOT)
    off = (1024 - inner) // 2
    blend = Image.new("L", (inner, inner), 0)
    blend.paste(rounded_mask(inner - 80, 140), (40, 40))
    canvas.paste(out, (off, off), blend.filter(ImageFilter.GaussianBlur(28)))
    return canvas


def rounded(img, radius_frac=0.2):
    s = img.size[0]
    out = img.convert("RGBA")
    out.putalpha(rounded_mask(s, int(s * radius_frac)))
    return out


def fit_16x9(path, w, h):
    im = Image.open(path).convert("RGB")
    iw, ih = im.size
    target = w / h
    if iw / ih > target:
        nw = round(ih * target); box = ((iw - nw) // 2, 0, (iw - nw) // 2 + nw, ih)
    else:
        nh = round(iw / target); box = (0, (ih - nh) // 2, iw, (ih - nh) // 2 + nh)
    return im.crop(box).resize((w, h), Image.LANCZOS)


def save(img, *parts):
    p = os.path.join(*parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    img.save(p, optimize=True)
    print("wrote", os.path.relpath(p, ROOT), img.size)


def main():
    sq = square_icon()
    rr = rounded(sq)
    save(sq, HERE, "harbour-icon-1024.png")
    save(rr, HERE, "harbour-icon-rounded-1024.png")
    save(fit_16x9(os.path.join(SRC, "banner-16x9.jpg"), 1280, 720), HERE, "harbour-banner-1280.png")
    wide = Image.open(os.path.join(SRC, "banner-wide.jpg")).convert("RGB")
    save(wide.resize((1600, round(1600 * wide.height / wide.width)), Image.LANCZOS), HERE, "harbour-header-wide.png")

    # Android (both the original app and v2)
    banner = fit_16x9(os.path.join(SRC, "banner-16x9.jpg"), 320, 180)
    for res in ANDROID_RES:
        for dpi, px in (("mdpi", 48), ("hdpi", 72), ("xhdpi", 96), ("xxhdpi", 144), ("xxxhdpi", 192)):
            save(rr.resize((px, px), Image.LANCZOS), res, f"mipmap-{dpi}", "ic_launcher.png")
        save(sq.resize((160, 160), Image.LANCZOS).convert("RGBA"), res, "drawable-nodpi", "logo.png")
        save(banner, res, "drawable-xhdpi", "banner.png")

    # iPhone web app (iOS rounds the corners itself, so these stay square and opaque)
    save(sq.resize((180, 180), Image.LANCZOS), WEB, "apple-touch-icon.png")
    save(sq.resize((192, 192), Image.LANCZOS), WEB, "icon-192.png")
    save(sq.resize((512, 512), Image.LANCZOS), WEB, "icon-512.png")
    # Maskable: mark shrunk into the 80% safe zone on the navy background
    m = gradient(512, NAVY_TOP, NAVY_BOT)
    inner = sq.resize((410, 410), Image.LANCZOS)
    m.paste(inner, (51, 51), rounded_mask(410, 60, feather=6))
    save(m, WEB, "icon-maskable-512.png")
    save(rr.resize((32, 32), Image.LANCZOS), WEB, "favicon-32.png")
    rr.resize((64, 64), Image.LANCZOS).save(os.path.join(WEB, "favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    print("wrote branding/web/favicon.ico")
    portrait = Image.open(os.path.join(SRC, "portrait.jpg")).convert("RGB")
    save(portrait.resize((1179, round(1179 * portrait.height / portrait.width)), Image.LANCZOS), WEB, "launch-1179.png")


if __name__ == "__main__":
    main()
