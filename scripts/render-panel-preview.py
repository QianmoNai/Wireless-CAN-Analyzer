#!/usr/bin/env python3
"""Build and render the real C panel UI with simulated data; requires C compiler and Pillow."""
from pathlib import Path
import subprocess
import tempfile
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='can-panel-preview-') as temporary:
    output = Path(temporary)
    binary = output/'render-panel'
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Ifirmware/main',
                    'tests/render_board_ui.c', 'firmware/main/board_ui_render.c',
                    'firmware/main/board_ui_model.c', 'firmware/main/board_ui_font.c',
                    '-o', str(binary)], cwd=ROOT, check=True)
    subprocess.run([str(binary), str(output)], check=True)
    # Show every direction: the outer physical button numbers change at 180/270.
    sheet = Image.new('RGB', (728, 832), '#0c1320')
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=18)
    draw.text((10, 14), '0 deg - overview / settings', fill='#c7d5e6', font=font)
    draw.text((366, 14), '180 deg - overview / settings', fill='#c7d5e6', font=font)
    for angle, column in [(0, 0), (180, 2)]:
        for page in range(2):
            portrait = Image.open(output/f'panel-{angle}-{page}{"-sound" if page==1 else ""}.ppm')
            sheet.paste(portrait, (10+(column+page)*178, 44))
    for angle, top in [(90, 390), (270, 620)]:
        draw.text((16, top), f'{angle} deg - firmware renderer, simulated data', fill='#c7d5e6', font=font)
        for page in range(2):
            landscape = Image.open(output/f'panel-{angle}-{page}{"-sound" if page==1 else ""}.ppm')
            sheet.paste(landscape, (16+page*352, top+28))
    target = ROOT/'docs/local-panel-preview.png'
    sheet.save(target)
    print(target)
    rotation_sheet = Image.new('RGB', (728, 480), '#0c1320')
    draw = ImageDraw.Draw(rotation_sheet)
    for angle, left, top in [(0, 10, 14), (180, 188, 14), (90, 388, 14), (270, 388, 240)]:
        draw.text((left, top), f'{angle} deg - editing', fill='#c7d5e6', font=font)
        frame = Image.open(output/f'panel-{angle}-1-rotation.ppm')
        rotation_sheet.paste(frame, (left, top+28))
    draw.text((10, 452), 'Firmware renderer - simulated data', fill='#c7d5e6', font=font)
    target = ROOT/'docs/local-panel-rotation-preview.png'
    rotation_sheet.save(target)
    print(target)

    network_sheet = Image.new('RGB', (728, 860), '#0c1320')
    draw = ImageDraw.Draw(network_sheet)
    for i, (angle, suffix, page, caption) in enumerate([(270,'',0,'BLE / blue'),(270,'-wifi',0,'Wi-Fi / purple'),(270,'',1,'BLE settings'),(270,'-wifi',1,'Wi-Fi settings')]):
        x=16+(i%2)*352;y=14+(i//2)*210
        draw.text((x,y),caption,fill='#c7d5e6',font=font)
        network_sheet.paste(Image.open(output/f'panel-{angle}-{page}{suffix}.ppm'),(x,y+26))
    for i,(suffix,caption) in enumerate([('','BLE'),('-wifi','Wi-Fi'),('-pairing','Secure pairing')]):
        x=16+i*240;draw.text((x,444),caption,fill='#c7d5e6',font=font)
        network_sheet.paste(Image.open(output/f'panel-0-1{suffix}.ppm'),(x,478))
    draw.text((16,826),'Firmware renderer - simulated data',fill='#c7d5e6',font=font)
    target=ROOT/'docs/local-panel-network-preview.png';network_sheet.save(target);print(target)

    volume_sheet = Image.new('RGB', (1132, 856), '#0c1320')
    draw = ImageDraw.Draw(volume_sheet)
    for column, level in enumerate(['low', 'medium', 'high']):
        x = 16+column*372
        draw.text((x, 14), f'Volume: {level} - 0 / 180 deg', fill='#c7d5e6', font=font)
        for angle, offset in [(0, 0), (180, 180)]:
            frame = Image.open(output/f'panel-{angle}-1-volume-{level}.ppm')
            volume_sheet.paste(frame, (x+offset, 44))
        for angle, top in [(90, 390), (270, 608)]:
            draw.text((x, top), f'{angle} deg', fill='#c7d5e6', font=font)
            frame = Image.open(output/f'panel-{angle}-1-volume-{level}.ppm')
            volume_sheet.paste(frame, (x, top+28))
    draw.text((16, 824), 'Firmware renderer - simulated data', fill='#c7d5e6', font=font)
    target = ROOT/'docs/local-panel-volume-preview.png'
    volume_sheet.save(target)
    print(target)
