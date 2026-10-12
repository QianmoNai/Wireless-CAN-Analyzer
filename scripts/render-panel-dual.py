#!/usr/bin/env python3
"""Render dual CAN screens with the firmware renderer and simulated data."""
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="can-dual-preview-") as temporary:
    output = Path(temporary)
    binary = output / "render-panel"
    subprocess.run([
        "cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Ifirmware/main",
        "tests/render_board_ui.c", "firmware/main/board_ui_render.c",
        "firmware/main/board_ui_model.c", "firmware/main/board_ui_font.c",
        "-o", str(binary),
    ], cwd=ROOT, check=True)
    subprocess.run([str(binary), str(output)], check=True)
    sheet = Image.new("RGB", (1060, 1050), "#0c1320")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=17)

    def frame(angle, suffix, x, y, caption):
        draw.text((x, y), caption, fill="#c7d5e6", font=font)
        sheet.paste(Image.open(output / f"panel-{angle}-1-{suffix}.ppm"), (x, y + 26))

    for i, (angle, role) in enumerate([(0, "a"), (0, "b"), (180, "a"), (180, "b")]):
        frame(angle, f"dual-{role}", 16 + i * 180, 14, f"{angle} deg / {role.upper()}")
    frame(0, "dual-check", 784, 14, "Compare code / A")
    rows = [
        [(90, "dual-a", "90 deg / A green"),
         (90, "dual-b", "90 deg / B yellow"),
         (270, "dual-search", "Search / A")],
        [(270, "dual-a", "270 deg / A green"),
         (270, "dual-b", "270 deg / B yellow"),
         (270, "dual-wait", "Waiting / B yellow")],
        [(270, "dual-check", "Compare code / A"),
         (270, "dual-online", "Connected / A"),
         (270, "dual-unbind", "Unbind confirmation / A")],
    ]
    for row, frames in enumerate(rows):
        for column, (angle, suffix, caption) in enumerate(frames):
            frame(angle, suffix, 16 + column * 348, 374 + row * 216, caption)
    draw.text((16, 1020), "Firmware renderer - simulated data, not a hardware capture",
              fill="#c7d5e6", font=font)
    target = ROOT / "docs/local-panel-dual-preview.png"
    sheet.save(target)
    print(target)
