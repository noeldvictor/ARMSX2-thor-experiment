#!/usr/bin/env python3
"""Validate the Android/SVG paths and render preview.png beside this script.

Run from any directory: python path/to/make_icon_preview.py
Requires Pillow (python -m pip install Pillow). Uses CairoSVG when installed and
loadable; otherwise a small polygon renderer handles this SVG's M/L/Z paths,
even-odd holes and rotation. No API, downloads, build or other output files.

The 108dp layers are cropped to Android's central 72dp viewport before masking.
Circle, superellipse (n=4) and rounded-square (radius=22%) are representative
launcher masks. Both 192px and actual 48px examples are rendered independently.
The themed example uses an illustrative wallpaper tint; Android chooses it.
The old icon uses the source PNG and main's existing 18% foreground inset.
"""

from __future__ import annotations

import argparse
import io
import math
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFont


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RES = ROOT / "platforms/android/app/src/github/res"
MAIN = ROOT / "platforms/android/app/src/main/res"
SVG = "{http://www.w3.org/2000/svg}"
ANDROID = "{http://schemas.android.com/apk/res/android}"
SUPERSAMPLE = 4
TOKEN = re.compile(r"[MLZ]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?")
THEME_BG = "#F5DDB6"
THEME_FG = "#483317"


def rings(path: str) -> list[list[tuple[float, float]]]:
    """Parse the deliberately small, explicit absolute-command path vocabulary."""
    if TOKEN.sub("", path).strip(" ,\t\r\n"):
        raise ValueError("Preview renderer supports only absolute M, L and Z")
    tokens = TOKEN.findall(path)
    result, current = [], None
    i = 0
    while i < len(tokens):
        command = tokens[i]
        if command == "Z":
            if current is None or len(current) < 3:
                raise ValueError("Path must close a polygon with at least three points")
            result.append(current)
            current = None
            i += 1
            continue
        if command not in ("M", "L") or i + 2 >= len(tokens):
            raise ValueError("Expected M/L followed by an explicit x,y pair")
        point = (float(tokens[i + 1]), float(tokens[i + 2]))
        if command == "M":
            if current is not None:
                raise ValueError("Close each contour before starting another")
            current = [point]
        elif current is None:
            raise ValueError("L before M")
        else:
            current.append(point)
        i += 3
    if current is not None or not result:
        raise ValueError("Expected closed nonempty paths")
    return result


def checked(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate() -> ET.Element:
    """Check path syntax, layer parity, rotations, safe circle and references."""
    svg = ET.parse(HERE / "ic_launcher.svg").getroot()
    checked(svg.get("viewBox") == "0 0 108 108", "SVG viewport must be 108x108")
    background = svg.find(f"{SVG}path")
    group = svg.find(f"{SVG}g")
    checked(group.get("transform") == "rotate(45 54 54)", "Unexpected SVG transform")
    paths = list(group)
    checked(len(paths) == 2, "Expected hammer and cyan tip")
    for layer in ("background", "foreground", "monochrome"):
        xml = ET.parse(RES / "drawable" / f"ic_launcher_{layer}.xml").getroot()
        checked(xml.tag == "vector", f"{layer}: expected VectorDrawable")
        for key in ("width", "height", "viewportWidth", "viewportHeight"):
            expected = "108dp" if key in ("width", "height") else "108"
            checked(xml.get(ANDROID + key) == expected, f"{layer}: incorrect {key}")
        android_group = xml.find("group")
        if layer != "background":
            for key, value in (("rotation", "45"), ("pivotX", "54"), ("pivotY", "54")):
                checked(android_group.get(ANDROID + key) == value, f"{layer}: {key}")
        actual = xml.findall(".//path")
        expected_paths = [background] if layer == "background" else paths
        if layer == "monochrome":
            expected_paths = paths[:1]
        checked(len(actual) == len(expected_paths), f"{layer}: path count mismatch")
        for android_path, svg_path in zip(actual, expected_paths):
            data = android_path.get(ANDROID + "pathData")
            checked(data == svg_path.get("d"), f"{layer}: Android/SVG geometry differs")
            expected_color = "#FFFFFF" if layer == "monochrome" else svg_path.get("fill")
            checked(android_path.get(ANDROID + "fillColor") == expected_color,
                    f"{layer}: fill differs")
            rule = android_path.get(ANDROID + "fillType", "nonZero").lower()
            checked(rule == svg_path.get("fill-rule", "nonzero"), f"{layer}: fill rule")
            contours = rings(data)
            if layer != "background":
                # Rotation about (54,54) preserves distance. A disk is convex, so
                # all straight edges/interiors also fit when all vertices fit.
                radius = max(math.hypot(x - 54, y - 54) for ring in contours for x, y in ring)
                checked(radius <= 33, f"{layer}: glyph exceeds the 66dp safe circle")
    for folder in ("mipmap-anydpi", "mipmap-anydpi-v26"):
        for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
            xml = ET.parse(RES / folder / name).getroot()
            checked(xml.tag == "adaptive-icon" and len(xml) == 3, f"{folder}/{name}")
            for layer in ("background", "foreground", "monochrome"):
                node = xml.find(layer)
                checked(node is not None and node.get(ANDROID + "drawable") ==
                        f"@drawable/ic_launcher_{layer}", f"{folder}/{name}: {layer}")
    return svg


def minimal_render(svg: ET.Element, pixels: int) -> Image.Image:
    """Rasterize these flat polygons at high resolution, preserving real holes."""
    canvas = Image.new("RGBA", (pixels, pixels))
    scale = pixels / 108
    paths = [(svg.find(f"{SVG}path"), 0)]
    paths.extend((path, math.pi / 4) for path in svg.find(f"{SVG}g"))
    for path, angle in paths:
        coverage = Image.new("L", canvas.size)
        for contour in rings(path.get("d")):
            points = []
            for x, y in contour:
                dx, dy = x - 54, y - 54
                x = 54 + dx * math.cos(angle) - dy * math.sin(angle)
                y = 54 + dx * math.sin(angle) + dy * math.cos(angle)
                points.append((x * scale, y * scale))
            polygon = Image.new("L", canvas.size)
            ImageDraw.Draw(polygon).polygon(points, fill=255)
            if path.get("fill-rule") == "evenodd":
                coverage = ImageChops.difference(coverage, polygon)
            else:
                coverage = ImageChops.lighter(coverage, polygon)
        layer = Image.new("RGBA", canvas.size, path.get("fill"))
        layer.putalpha(coverage)
        canvas = Image.alpha_composite(canvas, layer)
    return canvas


def render(svg: ET.Element, pixels: int, backend: str) -> Image.Image:
    if backend == "cairo":
        import cairosvg
        return Image.open(io.BytesIO(cairosvg.svg2png(
            bytestring=ET.tostring(svg), output_width=pixels, output_height=pixels
        ))).convert("RGBA")
    return minimal_render(svg, pixels)


def themed(svg: ET.Element) -> ET.Element:
    result = ET.fromstring(ET.tostring(svg))
    result.find(f"{SVG}path").set("fill", THEME_BG)
    group = result.find(f"{SVG}g")
    group.remove(list(group)[1])
    list(group)[0].set("fill", THEME_FG)
    return result


def old_layer(pixels: int) -> Image.Image:
    """Reproduce the current upstream foreground inset and 315-degree gradient."""
    background = ET.parse(MAIN / "drawable/ic_launcher_background.xml").getroot()
    gradient = background.find("gradient")
    checked(gradient.get(ANDROID + "angle") == "315", "Old background gradient changed")
    colors = [ImageColor.getrgb(gradient.get(ANDROID + key))
              for key in ("startColor", "centerColor", "endColor")]
    ramp = []
    for diagonal in range(2 * pixels - 1):
        t = diagonal / (pixels - 1)
        a, b = (colors[0], colors[1]) if t <= 1 else (colors[1], colors[2])
        t = t if t <= 1 else t - 1
        ramp.append(tuple(round(v + (w - v) * t) for v, w in zip(a, b)) + (255,))
    image = Image.new("RGBA", (pixels, pixels))
    image.putdata([ramp[x + y] for y in range(pixels) for x in range(pixels)])
    inset_xml = ET.parse(MAIN / "drawable/ic_launcher_foreground.xml").getroot()
    inset = round(pixels * float(inset_xml.get(ANDROID + "inset").rstrip("%")) / 100)
    with Image.open(MAIN / "drawable/savetowerforeground.png") as source:
        old = source.convert("RGBA").resize((pixels - 2 * inset,) * 2, Image.Resampling.LANCZOS)
    image.alpha_composite(old, (inset, inset))
    return image


def mask_icon(layer: Image.Image, size: int, shape: str) -> Image.Image:
    # The adaptive mask covers the central 72 of the layer's 108 units.
    edge = layer.width // 6
    icon = layer.crop((edge, edge, layer.width - edge, layer.height - edge))
    pixels = size * SUPERSAMPLE
    checked(icon.size == (pixels, pixels), "Incorrect adaptive crop size")
    mask = Image.new("L", icon.size)
    draw = ImageDraw.Draw(mask)
    box = (0, 0, pixels - 1, pixels - 1)
    if shape == "circle":
        draw.ellipse(box, fill=255)
    elif shape == "rounded-square":
        draw.rounded_rectangle(box, radius=pixels * 0.22, fill=255)
    elif shape == "squircle":
        r = (pixels - 1) / 2
        points = []
        for i in range(720):
            angle = i * math.tau / 720
            c, s = math.cos(angle), math.sin(angle)
            points.append((r + r * math.copysign(abs(c) ** 0.5, c),
                           r + r * math.copysign(abs(s) ** 0.5, s)))
        draw.polygon(points, fill=255)
    else:
        raise ValueError(shape)
    icon.putalpha(ImageChops.multiply(icon.getchannel("A"), mask))
    return icon.resize((size, size), Image.Resampling.LANCZOS)


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in (str(windows / ("segoeuib.ttf" if bold else "segoeui.ttf")),
                 "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
                 "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--renderer", choices=("auto", "cairo", "minimal"), default="auto")
    args = parser.parse_args()
    svg = validate()
    backend = args.renderer
    if backend == "auto":
        try:
            import cairosvg  # noqa: F401
            backend = "cairo"
        except (ImportError, OSError):
            backend = "minimal"
    canvas = Image.new("RGB", (1248, 912), "#090F13")
    draw = ImageDraw.Draw(canvas)
    draw.text((32, 22), "ARMSX2 THOR", font=font(32, True), fill="#FFB039")
    draw.text((32, 70), "A hammer forged for the fork.  /  Adaptive launcher icon study",
              font=font(16), fill="#B7C6CC")
    for index, (color, label) in enumerate((("#FFB039", "AMBER"), ("#42E0FF", "CYAN"),
                                           ("#060B0E", "INK"))):
        x = 914 + index * 104
        draw.rounded_rectangle((x, 32, x + 26, 58), radius=6, fill=color, outline="#30414B")
        draw.text((x, 70), label, font=font(11, True), fill="#B7C6CC")
    for index, (title, shape) in enumerate((("CIRCLE", "circle"), ("SQUIRCLE", "squircle"),
                                           ("ROUNDED SQUARE", "rounded-square"),
                                           ("MONOCHROME", "circle"))):
        left = 32 + index * 304
        center = left + 136
        draw.rounded_rectangle((left, 120, left + 272, 838), radius=18,
                               fill="#111B22", outline="#24343E")
        draw.text((center, 138), title, font=font(15, True), fill="#DCE6EB", anchor="mt")
        source = themed(svg) if index == 3 else svg
        for size, y in ((192, 184), (48, 724)):
            pixels = size * SUPERSAMPLE * 3 // 2
            new = mask_icon(render(source, pixels, backend), size, shape)
            old = mask_icon(old_layer(pixels), size, shape)
            new_x = center - size // 2 if size == 192 else center - 72
            old_x = center - size // 2 if size == 192 else center + 24
            canvas.paste(new, (new_x, y), new)
            canvas.paste(old, (old_x, 446 if size == 192 else y), old)
        draw.text((center, 395), "THOR / 192 PX", font=font(13, True), fill="#FFB039", anchor="mt")
        old_label = "UPSTREAM / COLOR REFERENCE" if index == 3 else "UPSTREAM / 192 PX"
        draw.text((center, 656), old_label, font=font(12), fill="#91A5AF", anchor="mt")
        draw.line((left + 24, 697, left + 248, 697), fill="#24343E")
        draw.text((center - 48, 785), "THOR", font=font(11, True), fill="#FFB039", anchor="mt")
        draw.text((center + 48, 785), "UPSTREAM", font=font(11), fill="#91A5AF", anchor="mt")
        draw.text((center, 813), "ACTUAL 48 PX", font=font(10), fill="#718994", anchor="mt")
    draw.text((32, 858), "108dp layers / central 72dp launcher crop / glyph inside the 66dp safe circle",
              font=font(13), fill="#A4B6BF")
    draw.text((32, 881), "Monochrome uses one silhouette with a transparent cutout. Wallpaper tint and masks are illustrative.",
              font=font(12), fill="#718994")
    output = HERE / "preview.png"
    canvas.save(output)
    radius = max(math.hypot(x - 54, y - 54) for path in svg.find(f"{SVG}g")
                 for contour in rings(path.get("d")) for x, y in contour)
    print(f"Validated all seven Android XML files against SVG; glyph radius: {radius:.3f}dp < 33dp.")
    print(f"Renderer: {backend}. Preview: {output}")


if __name__ == "__main__":
    main()
