#!/usr/bin/env python3
"""Preview firmware animations; simulated timing/data, not a device recording."""
from pathlib import Path
import subprocess
import tempfile
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='can-panel-animation-') as temporary:
    output = Path(temporary)
    binary = output/'render-animation'
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Ifirmware/main',
                    'tests/render_board_ui_animation.c', 'firmware/main/board_ui_render.c',
                    'firmware/main/board_ui_animation.c', 'firmware/main/board_ui_model.c',
                    'firmware/main/board_ui_font.c', '-o', str(binary)], cwd=ROOT, check=True)
    subprocess.run([str(binary), str(output)], check=True)
    font = ImageFont.load_default(size=16)
    frames = []
    for time in range(0, 4201, 50):
        sheet = Image.new('RGB', (544, 390), '#0c1320')
        draw = ImageDraw.Draw(sheet)
        draw.text((16, 12), '0 deg', fill='#c7d5e6', font=font)
        draw.text((208, 12), '270 deg', fill='#c7d5e6', font=font)
        sheet.paste(Image.open(output/f'animation-0-{time:04}.ppm'), (16, 40))
        sheet.paste(Image.open(output/f'animation-270-{time:04}.ppm'), (208, 40))
        draw.text((208, 240), 'Page slide / menu motion', fill='#c7d5e6', font=font)
        draw.text((208, 266), 'BLE blue -> Wi-Fi purple', fill='#c7d5e6', font=font)
        draw.text((16, 370), 'Firmware renderer - simulated animation', fill='#8c9bac', font=font)
        frames.append(sheet)
    target = ROOT/'docs/local-panel-animation.gif'
    # One common palette prevents frame-by-frame quantization from flickering.
    atlas = Image.new('RGB', (544, 390*len(frames)))
    for index, frame in enumerate(frames):
        atlas.paste(frame, (0, 390*index))
    palette = atlas.quantize(colors=256)
    images = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    images[0].save(target, save_all=True, append_images=images[1:], duration=50,
                   loop=0, optimize=False, disposal=1)
    print(target)
    times = [400, 450, 500, 600, 1000, 1050, 1100, 1200, 1850, 1900, 2000, 2150]
    sheet = Image.new('RGB', (1344, 656), '#0c1320')
    draw = ImageDraw.Draw(sheet)
    for index, time in enumerate(times):
        x = 12+(index%4)*334
        y = 10+(index//4)*214
        draw.text((x, y), f'{time} ms', fill='#c7d5e6', font=font)
        sheet.paste(Image.open(output/f'animation-270-{time:04}.ppm'), (x, y+24))
    target = ROOT/'docs/local-panel-animation-frames.png'
    sheet.save(target)
    print(target)
