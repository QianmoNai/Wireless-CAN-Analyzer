#!/usr/bin/env python3
"""Generate a Chinese MP4 showcase from actual firmware drawing code (simulated inputs)."""
from pathlib import Path
import argparse
import subprocess
import tempfile
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT = '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc'
SCENES = [
    (0, '01  CAN 总览', '蓝牙连接 · 蓝色主题', ['接收 / 发送帧率实时更新', '总线负载、失败统计一屏查看'], 'BT2 短按 → 进入设置'),
    (4, '02  亮度调节', '中键编辑 · 两侧调整', ['进入设置时整页平滑滑动', '亮度按 10% 步进调节'], '上方 / 左侧键增加，另一侧减少'),
    (8, '03  声音开关', '选中后，中键直接切换', ['开 / 关即时切换', '不需要先进入编辑状态'], 'BT2 短按 → 关 / 开'),
    (11, '04  三档音量', '低 / 中 / 高循环选择', ['选中框随菜单移动', '选中后按中键循环切换档位'], 'BT2 短按 → 下一档'),
    (14.5, '05  Wi-Fi 模式', '蓝色 → 紫色', ['蓝牙与 Wi-Fi 保留为可选模式', '总览显示局域网 IP 地址'], '切换模式后，长按 BT2 返回总览'),
    (20, '06  双机 A 端', '绿色主题 · 按键配对', ['进入配对菜单，搜索附近 B 端', '两端核对六位配对码'], '演示搜索 → 核对配对码 → 已连接'),
    (28.5, '07  双机转发', 'A 端 · 已连接', ['两端各连接一条 CAN 总线', '通过 ESP-NOW 双向转发'], '此模式无需手机 App 连接'),
    (31, '08  双机 B 端', '黄色主题 · 等待配对', ['B 端等待 A 端发起配对', '连接后查看接收 / 发送状态'], 'A 端绿色，B 端黄色'),
    (34.7, '08  双机 B 端', '黄色主题 · 已连接', ['配对完成，返回 CAN 总览', '查看双向转发的接收 / 发送状态'], 'A 端绿色，B 端黄色'),
    (38, '09  四方向显示', '0° / 90° / 180° / 270°', ['旋转后重新布局屏幕内容', '底部按键提示跟随当前方向'], '方向编辑：BT1 顺时针，BT3 逆时针'),
]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'dist/panel-showcase/CAN-Scope-屏幕功能展示.mp4')
    args=parser.parse_args();args.output.parent.mkdir(parents=True,exist_ok=True)
    fonts={n:ImageFont.truetype(FONT,n) for n in (18,20,24,28,38)}
    snapshots=[]
    with tempfile.TemporaryDirectory(prefix='can-showcase-') as temp:
        folder=Path(temp);binary=folder/'renderer'
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-Ifirmware/main','tests/render_board_ui_showcase.c','firmware/main/board_ui_render.c','firmware/main/board_ui_animation.c','firmware/main/board_ui_model.c','firmware/main/board_ui_font.c','-o',str(binary)],cwd=ROOT,check=True)
        subprocess.run([str(binary),str(folder)],check=True)
        process=subprocess.Popen(['ffmpeg','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','1280x900','-r','20','-i','-','-an','-c:v','libx264','-preset','medium','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',str(args.output)],stdin=subprocess.PIPE)
        for frame in range(920):
            t=frame/20;scene=max((s for s in SCENES if s[0]<=t),key=lambda s:s[0]);_,title,subtitle,lines,action=scene
            canvas=Image.new('RGB',(1280,900),'#0c1320');d=ImageDraw.Draw(canvas)
            d.text((48,26),'CAN Scope  /  屏幕功能展示',font=fonts[38],fill='#eff5ff')
            d.text((48,82),'ESP32-S3  ·  ST7789 172 × 320  ·  中文界面',font=fonts[20],fill='#94a6bc')
            if t<38:
                for view,x,y,label in [(0,64,165,'0°  竖屏'),(1,536,165,'270°  横屏')]:
                    d.text((x,y-40),label,font=fonts[24],fill='#b8c9dd')
                    with Image.open(folder/f'view-{view}-{frame:04}.ppm') as im:
                        im=im.resize((im.width*2,im.height*2),Image.Resampling.NEAREST)
                        d.rounded_rectangle((x-8,y-8,x+im.width+8,y+im.height+8),radius=8,fill='#202d43')
                        canvas.paste(im,(x,y))
                tx,ty=536,548
            else:
                angle=int((t-38)//2)*90
                with Image.open(folder/f'view-0-{frame:04}.ppm') as im:
                    im=im.resize((im.width*2,im.height*2),Image.Resampling.NEAREST)
                    x=48+(700-im.width)//2;y=158+(640-im.height)//2
                    d.text((64,120),f'{angle}°  当前屏幕方向',font=fonts[24],fill='#b8c9dd')
                    d.rounded_rectangle((x-8,y-8,x+im.width+8,y+im.height+8),radius=8,fill='#202d43');canvas.paste(im,(x,y))
                tx,ty=790,300
            accent='#5ad8ff' if t<15.2 or t>=38 else '#b48aff' if t<20.9 else '#61e0a7' if t<31.8 else '#ffd75e'
            d.rectangle((tx,ty,tx+5,ty+33),fill=accent)
            d.text((tx+20,ty-5),title,font=fonts[28],fill='#eef5ff')
            d.text((tx,ty+49),subtitle,font=fonts[24],fill=accent)
            for n,line in enumerate(lines):d.text((tx,ty+95+36*n),line,font=fonts[20],fill='#b5c5d9')
            # Long instructions wrap in the narrower rotation caption column.
            actionlines=[action] if t<38 else ['方向编辑：','BT1 顺时针 · BT3 逆时针']
            for n,line in enumerate(actionlines):d.text((tx,ty+180+30*n),line,font=fonts[20],fill='#8fa6be')
            d.line((48,840,1232,840),fill='#26354a',width=2)
            d.line((48,840,48+int(1184*(frame+1)/920),840),fill=accent,width=3)
            d.text((48,855),'CAN Scope  ·  中文屏幕与按键操作',font=fonts[18],fill='#92a3b8')
            d.text((1100,855),f'{int(t):02d} / 46 秒',font=fonts[18],fill='#92a3b8')
            process.stdin.write(canvas.tobytes())
            if frame in (40,110,176,250,370,510,600,720,840):snapshots.append(canvas.copy())
            if frame==40:canvas.save(args.output.with_suffix('.png'))
        process.stdin.close()
        if process.wait():raise RuntimeError('ffmpeg failed')
    contact=Image.new('RGB',(1280,900),'#0c1320')
    for i,im in enumerate(snapshots):contact.paste(im.resize((426,300)),((i%3)*426,(i//3)*300))
    contact.save(args.output.with_name('功能展示分镜.png'))
    args.output.with_suffix('.txt').write_text('屏幕功能展示，46 秒，1280×900，20 fps，无音轨。\n来自固件实际绘图代码，数据、按键输入、无线连接与配对状态均为模拟。视频帧率不是实机帧率测量。\n\n'+ '\n'.join(f'{start:05.1f}s  {title}：{subtitle}' for start,title,subtitle,_,_ in SCENES)+'\n',encoding='utf8')
    print(args.output)
if __name__=='__main__':main()
