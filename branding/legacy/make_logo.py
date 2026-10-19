"""
Harbour logo generator: the Sydney Harbour Bridge whose arch is a strip of film,
with a play-button sun rising under it. Writes SVGs + every Android PNG size.

    pip install cairosvg && python branding/make_logo.py
"""
import math
import os

import cairosvg

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "..", "android", "app", "src", "main", "res")

NAVY_TOP, NAVY_BOT = "#0E2A47", "#06111D"
DUSK = "#F08A4B"
TEAL, TEAL_DARK = "#2FB6A0", "#1B7F70"
SUN = "#F5B942"
STONE, STONE_DARK = "#D9C49A", "#A8915F"
STEEL = "#9FB3C8"


def arch(cx, half, base_y, peak_y, steps=60):
    """Points on a parabola from (cx-half, base_y) up to (cx, peak_y) and down again."""
    k = (base_y - peak_y) / half ** 2
    return [(cx - half + 2 * half * i / steps, peak_y + k * (2 * half * i / steps - half) ** 2) for i in range(steps + 1)]


def path(pts):
    return "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)


def bridge_group(x0=0, y0=0, s=1.0):
    """The emblem drawn in a 512x512 box; returns an SVG <g>."""
    cx = 256
    deck_y = 318
    upper = arch(cx, 150, 300, 132)    # film-strip top chord
    lower = arch(cx, 150, 350, 190)    # lower chord
    parts = []

    # sunset glow + play-button sun under the arch
    parts.append(f'<circle cx="{cx}" cy="262" r="92" fill="url(#glow)"/>')

    # truss: zig-zag between the two chords
    zig = []
    for i in range(0, len(upper), 3):
        zig.append(upper[i] if (i // 3) % 2 == 0 else lower[i])
    parts.append(f'<path d="{path(zig)}" fill="none" stroke="{TEAL_DARK}" stroke-width="4" stroke-linejoin="round"/>')
    parts.append(f'<path d="{path(lower)}" fill="none" stroke="{TEAL}" stroke-width="9" stroke-linecap="round"/>')

    # hangers from the lower chord down to the deck
    for i in range(6, len(lower) - 6, 4):
        x, y = lower[i]
        if y < deck_y - 6:
            parts.append(f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x:.1f}" y2="{deck_y}" stroke="{STEEL}" stroke-width="3"/>')

    # play-button sun, in front of the hangers
    parts.append(f'<circle cx="{cx}" cy="262" r="50" fill="{NAVY_TOP}"/>')
    parts.append(f'<circle cx="{cx}" cy="262" r="44" fill="{SUN}"/>')
    parts.append(f'<path d="M {cx-13},{262-20} L {cx-13},{262+20} L {cx+21},262 Z" fill="{NAVY_BOT}"/>')

    # film-strip top chord with sprocket holes
    parts.append(f'<path d="{path(upper)}" fill="none" stroke="{TEAL}" stroke-width="34" stroke-linecap="butt"/>')
    for i in range(2, len(upper) - 2, 3):
        (x1, y1), (x2, y2) = upper[i - 1], upper[i + 1]
        ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
        x, y = upper[i]
        for off in (-10, 10):
            parts.append(f'<rect x="-3.5" y="{off - 3.5}" width="7" height="7" rx="1.5" fill="{NAVY_TOP}" '
                         f'transform="translate({x:.1f},{y:.1f}) rotate({ang:.1f})"/>')

    # deck
    parts.append(f'<rect x="58" y="{deck_y - 6}" width="396" height="13" rx="3" fill="{STEEL}"/>')

    # granite pylons at both ends
    for px in (82, 386):
        parts.append(f'<rect x="{px}" y="236" width="44" height="132" rx="4" fill="{STONE}"/>')
        parts.append(f'<rect x="{px}" y="236" width="44" height="14" rx="4" fill="{STONE_DARK}"/>')
        parts.append(f'<rect x="{px + 15}" y="270" width="14" height="30" rx="7" fill="{STONE_DARK}"/>')

    # water + sun reflection
    parts.append(f'<ellipse cx="{cx}" cy="398" rx="54" ry="7" fill="{SUN}" opacity="0.55"/>')
    parts.append(f'<ellipse cx="{cx}" cy="416" rx="34" ry="4" fill="{SUN}" opacity="0.35"/>')
    for y, w in ((386, 300), (408, 220), (428, 140)):
        parts.append(f'<rect x="{cx - w / 2}" y="{y}" width="{w}" height="4" rx="2" fill="{TEAL}" opacity="0.35"/>')

    return f'<g transform="translate({x0},{y0}) scale({s})">' + "".join(parts) + "</g>"


DEFS = f'''<defs>
  <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="{NAVY_TOP}"/><stop offset="0.62" stop-color="#16314F"/>
    <stop offset="0.63" stop-color="{NAVY_BOT}"/><stop offset="1" stop-color="{NAVY_BOT}"/>
  </linearGradient>
  <radialGradient id="glow">
    <stop offset="0" stop-color="{DUSK}" stop-opacity="0.75"/><stop offset="1" stop-color="{DUSK}" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="bannerbg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="{NAVY_TOP}"/><stop offset="1" stop-color="{NAVY_BOT}"/>
  </linearGradient>
</defs>'''


def icon_svg():
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">{DEFS}'
            f'<rect width="512" height="512" rx="112" fill="url(#sky)"/>{bridge_group()}</svg>')


def banner_svg():
    # 16:9 TV banner: emblem left, wordmark right
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">{DEFS}'
            f'<rect width="1280" height="720" fill="url(#bannerbg)"/>'
            f'<rect x="70" y="104" width="512" height="512" rx="112" fill="url(#sky)"/>{bridge_group(70, 104)}'
            f'<text x="640" y="360" font-family="DejaVu Sans, Arial, sans-serif" font-weight="bold" font-size="112" '
            f'fill="#F2F4F7">Harbour</text>'
            f'<text x="644" y="430" font-family="DejaVu Sans, Arial, sans-serif" font-size="36" fill="{TEAL}">'
            f'your stack, on the big screen</text></svg>')


def main():
    os.makedirs(HERE, exist_ok=True)
    icon, banner = icon_svg(), banner_svg()
    open(os.path.join(HERE, "harbour-icon.svg"), "w").write(icon)
    open(os.path.join(HERE, "harbour-banner.svg"), "w").write(banner)
    cairosvg.svg2png(bytestring=icon.encode(), write_to=os.path.join(HERE, "harbour-icon-1024.png"),
                     output_width=1024, output_height=1024)
    cairosvg.svg2png(bytestring=banner.encode(), write_to=os.path.join(HERE, "harbour-banner-1280.png"),
                     output_width=1280, output_height=720)
    for folder, px in (("mipmap-mdpi", 48), ("mipmap-hdpi", 72), ("mipmap-xhdpi", 96),
                       ("mipmap-xxhdpi", 144), ("mipmap-xxxhdpi", 192)):
        os.makedirs(os.path.join(RES, folder), exist_ok=True)
        cairosvg.svg2png(bytestring=icon.encode(), write_to=os.path.join(RES, folder, "ic_launcher.png"),
                         output_width=px, output_height=px)
    os.makedirs(os.path.join(RES, "drawable-xhdpi"), exist_ok=True)
    cairosvg.svg2png(bytestring=banner.encode(), write_to=os.path.join(RES, "drawable-xhdpi", "banner.png"),
                     output_width=320, output_height=180)
    # in-app logo for the side menu
    os.makedirs(os.path.join(RES, "drawable-nodpi"), exist_ok=True)
    cairosvg.svg2png(bytestring=icon.encode(), write_to=os.path.join(RES, "drawable-nodpi", "logo.png"),
                     output_width=160, output_height=160)
    print("logo written")


if __name__ == "__main__":
    main()
