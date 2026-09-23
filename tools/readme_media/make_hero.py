#!/usr/bin/env python3
"""Rebuild the 1600x520 README banner with Pillow (pip install Pillow).

Run from any directory:
    python tools/readme_media/make_hero.py

The imagegen background/device plate is hero_plate.webp next to this script, so
rebuilding needs no API call. Only that plate is AI generated (Codex CLI image generation). The game pixels come from the repository's real Okage screenshot;
all banner typography is drawn with a real font. The 4:3 screenshot is pillarboxed
inside the 16:9 panel before applying a four-corner projective transform.

Segoe UI is used on Windows. Arial, Liberation Sans and DejaVu Sans are fallbacks;
pass --font-regular and --font-bold to use explicit font files elsewhere. Exact
reproduction requires the same fonts and Pillow version (created with 9.5.0).

Hardware shape reference: https://www.ayntec.com/products/ayn-thor
"""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, PngImagePlugin


ROOT = Path(__file__).resolve().parents[2]
SIZE = (1600, 520)
SUPERSAMPLE = 3
CYAN = "#42e0ff"
AMBER = "#ffb039"
BACKGROUND = "#060b0e"
LABEL = "AI vibe-coded / personal Thor experiment"
TAGLINE = "HD textures from the disc. 3x on the Thor. No support queue."

# Inner glass corners in final-banner pixels, clockwise from top left. Measured
# against this embedded plate, not against the device's outer shell/bezel.
SCREEN_QUAD = ((1007, 23), (1459, 43), (1436, 244), (978, 210))
TEXT_LEFT = 68
TEXT_RIGHT = 842
DEVICE_LEFT = 897

GENERATION_PROMPT = "Use case: product-mockup.\nCreate ONLY the background and device photography plate for a README banner, 1600 x 544 pixels, a wide panoramic image. The attached official AYN Thor product photo is a HARDWARE SHAPE REFERENCE: faithfully retain its real clamshell proportions, large 6-inch 16:9 top panel with slim black bezel, smaller central bottom screen, cylindrical hinge, left analog stick above D-pad, four right face buttons above right analog stick, understated black lower shell and real physical ports. Remove all lettering from the reference, including the THOR heading and button labels.\nScene: a single matte-black AYN Thor-style handheld open on a clean near-black desk, photographed at a very slight three-quarter angle with both screens clearly visible. Put the entire device exclusively in the RIGHTMOST 41 percent of the panoramic frame, fully contained approximately x=940..1550, y=38..487. The LEFT 56 percent must be uninterrupted almost-flat near-black (#060b0e) negative space for code typography later: absolutely no objects, outlines, contours, texture, lighting blobs or props behind it. A gentle transition at x=870..950 into the subtly lit desk on the right.\nScreen content: top display is a clean EMPTY dark charcoal rectangular glass screen, with four crisp straight visible edges for a later perspective composite. NO game image. No reflections obscuring the top display. Bottom display has a very dim minimal interface consisting only of faint cyan-gray bars, tiny sliders and dots, NO letters, numbers or words. It must remain clearly smaller than the upper display.\nLighting: restrained professional hardware photography with cyan #42e0ff rim lighting from the left and warm amber #ffb039 edging from the right; enough soft neutral light to resolve the controls and precise manufactured plastic. Subtle grounded shadow and slight desk sheen underneath. High-end editorial product shot, realistic and understated.\nConstraints: no text anywhere; no labels; no logos; no watermarks; no headline; no typography; no disc; no CDs; no floating cards; no accessories; no laptop; no lamp; no grid; no cables; no science-fiction HUD; no decorative clutter. Only one device on dark desk and ample clean left negative space. Do not render the game screenshot: a real screenshot will be added in Python later."


def font_path(explicit: Path | None, bold: bool) -> Path:
    if explicit is not None:
        if not explicit.is_file():
            raise FileNotFoundError(explicit)
        return explicit
    windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    candidates = (
        windows_fonts / ("segoeuib.ttf" if bold else "segoeui.ttf"),
        windows_fonts / ("arialbd.ttf" if bold else "arial.ttf"),
        Path("/Library/Fonts") / ("Arial Bold.ttf" if bold else "Arial.ttf"),
        Path("/usr/share/fonts/truetype/liberation2")
        / ("LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu")
        / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Supply --font-regular and --font-bold font paths.")


def perspective_coefficients(destination, source):
    """Solve Pillow's inverse homography using an 8x8 linear system.

    For each output (x, y), Pillow samples input ((a*x+b*y+c)/denominator,
    (d*x+e*y+f)/denominator), where denominator = g*x+h*y+1.
    """
    rows = []
    for (x, y), (u, v) in zip(destination, source):
        rows.append([x, y, 1, 0, 0, 0, -u*x, -u*y, u])
        rows.append([0, 0, 0, x, y, 1, -v*x, -v*y, v])
    for column in range(8):
        pivot = max(range(column, 8), key=lambda r: abs(rows[r][column]))
        if abs(rows[pivot][column]) < 1e-12:
            raise ValueError("Screen corners do not define a valid perspective.")
        rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        rows[column] = [v / divisor for v in rows[column]]
        for row in range(8):
            if row == column:
                continue
            factor = rows[row][column]
            rows[row] = [a - factor*b for a, b in zip(rows[row], rows[column])]
    coefficients = tuple(rows[r][8] for r in range(8))
    a, b, c, d, e, f, g, h = coefficients
    for (x, y), (u, v) in zip(destination, source):
        denominator = g*x + h*y + 1
        error = math.hypot((a*x+b*y+c)/denominator-u,
                           (d*x+e*y+f)/denominator-v)
        if error > 1e-5:
            raise ValueError("Screen transform failed its corner check.")
    return coefficients


def composite_screen(canvas: Image.Image, screenshot: Path) -> None:
    # Preserve every part of the screenshot and its aspect ratio. No crop,
    # invented game pixels, color grading, or stretching to fill widescreen.
    with Image.open(screenshot) as supplied:
        game = ImageOps.exif_transpose(supplied).convert("RGB")
    panel_size = (1600, 900)
    panel = Image.new("RGB", panel_size, "#030506")
    game = ImageOps.contain(game, panel_size, Image.Resampling.LANCZOS)
    panel.paste(game, ((panel.width-game.width)//2, (panel.height-game.height)//2))
    mask = Image.new("L", panel_size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 1599, 899), radius=25, fill=255)
    panel.putalpha(mask)
    destination = [(x*SUPERSAMPLE, y*SUPERSAMPLE) for x, y in SCREEN_QUAD]
    source = [(0, 0), (1600, 0), (1600, 900), (0, 900)]
    coefficients = perspective_coefficients(destination, source)
    warped = panel.transform(canvas.size, Image.Transform.PERSPECTIVE,
                             coefficients, Image.Resampling.BICUBIC)
    canvas.alpha_composite(warped)


def clean_text_area(plate: Image.Image) -> Image.Image:
    """Keep the entire copy column flat black; blend outside the copy bounds."""
    cover = Image.new("RGB", SIZE, BACKGROUND)
    mask = Image.new("L", SIZE, 0)
    draw = ImageDraw.Draw(mask)
    for x in range(916):
        t = max(0.0, min(1.0, (x-TEXT_RIGHT)/(916-TEXT_RIGHT)))
        opacity = round(255 * (1-t*t*(3-2*t)))
        draw.line((x, 0, x, SIZE[1]-1), fill=opacity)
    return Image.composite(cover, plate, mask)


def draw_type(canvas: Image.Image, regular: Path, bold: Path) -> None:
    scale = SUPERSAMPLE
    draw = ImageDraw.Draw(canvas)

    def fit_font(path: Path, size: int, text: str, width: int):
        for candidate_size in range(size, 23, -1):
            font = ImageFont.truetype(str(path), candidate_size*scale)
            bounds = font.getbbox(text)
            if bounds[2]-bounds[0] <= width*scale:
                return font
        raise ValueError(f"Text cannot fit at a readable size: {text}")

    def text_at(text: str, x: float, y: float, font, fill: str):
        # Place the actual ink, not the font's ascender box, at the given point.
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        width = (right-left)/scale
        if x+width > TEXT_RIGHT or x+width > DEVICE_LEFT-50:
            raise ValueError(f"Text would encroach on the device: {text}")
        draw.text((round(x*scale)-left, round(y*scale)-top), text,
                  font=font, fill=fill)
        return width, (bottom-top)/scale

    label_font = fit_font(regular, 28, LABEL, 680)
    bounds = label_font.getbbox(LABEL)
    label_width = (bounds[2]-bounds[0])/scale
    label_height = (bounds[3]-bounds[1])/scale
    pill = (TEXT_LEFT, 77, TEXT_LEFT+label_width+44, 133)
    draw.rounded_rectangle(tuple(round(v*scale) for v in pill), radius=17*scale,
                           fill="#07181e", outline=CYAN, width=2*scale)
    text_at(LABEL, TEXT_LEFT+22, 105-label_height/2, label_font, CYAN)

    for text, y, color in (("ARMSX2", 168, "#f4f8fa"),
                           ("THOR EXPERIMENT", 256, AMBER)):
        font = fit_font(bold, 80, text, TEXT_RIGHT-TEXT_LEFT)
        text_at(text, TEXT_LEFT, y, font, color)

    tagline_font = fit_font(regular, 28, TAGLINE, TEXT_RIGHT-TEXT_LEFT)
    text_at(TAGLINE, TEXT_LEFT, 355, tagline_font, "#c4d1d6")

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--screenshot", type=Path,
                        default=ROOT / "docs/media/okage-hd-pack-thor.jpg")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "docs/media/armsx2-thor-experiment-hero.png")
    parser.add_argument("--font-regular", type=Path)
    parser.add_argument("--font-bold", type=Path)
    parser.add_argument("--preview", type=Path,
                        help="Optionally save a 900px-wide review PNG at this path.")
    args = parser.parse_args()
    regular = font_path(args.font_regular, bold=False)
    bold = font_path(args.font_bold, bold=True)
    with Image.open(PLATE) as embedded:
        if embedded.size != SIZE:
            raise ValueError("Embedded plate dimensions changed; recalibrate screen corners.")
        plate = embedded.convert("RGB")
    plate = clean_text_area(plate)
    canvas = plate.resize(tuple(v*SUPERSAMPLE for v in SIZE),
                          Image.Resampling.LANCZOS).convert("RGBA")
    composite_screen(canvas, args.screenshot)
    draw_type(canvas, regular, bold)
    final = canvas.convert("RGB").resize(SIZE, Image.Resampling.LANCZOS)
    # Draw pixel-aligned accents after resampling to retain the exact brand colors.
    accents = ImageDraw.Draw(final)
    accents.rectangle((68, 423, 591, 426), fill=CYAN)
    accents.rectangle((604, 423, 741, 426), fill=AMBER)
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Description", "AI-generated device/background; real Okage screenshot; "
                      "Pillow typography. Rebuild with tools/readme_media/make_hero.py.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    final.save(args.output, format="PNG", optimize=True, pnginfo=metadata)
    if args.preview:
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        final.resize((900, round(520*900/1600)), Image.Resampling.LANCZOS).save(args.preview)
    print("Created 1600x520 PNG with a perspective-composited real screenshot and font typography.")
    print(f"Fonts: {regular.name}, {bold.name}")
    print(f"Result: {args.output.resolve()}")


# Generated once with the built-in imagegen tool and the prompt above, then
# center-cropped proportionally to 1600x520. Embedded losslessly for offline reruns.
PLATE = Path(__file__).resolve().parent / "hero_plate.webp"  # the AI-generated device/background plate


if __name__ == "__main__":
    main()
