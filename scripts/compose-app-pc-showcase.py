#!/usr/bin/env python3
"""Interleave existing Android showcase with captured Qt desktop UI clips."""
from pathlib import Path
import subprocess,json
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'dist/combined-showcase'
source=ROOT/'dist/android-showcase/CAN-Scope-安卓App功能介绍.mp4'
result=OUT/'CAN-Scope-安卓与PC联合功能介绍.mp4'
fonts={n:ImageFont.truetype('/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',n) for n in (18,20,40)}
report=json.loads((OUT/'pc-capture.json').read_text());assert report['frames']==360 and not report['js_errors'],report
size=1280*1000*3
read=subprocess.Popen(['ffmpeg','-v','error','-i',str(source),'-f','rawvideo','-pix_fmt','rgb24','-'],stdout=subprocess.PIPE)
write=subprocess.Popen(['ffmpeg','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','1280x1000','-r','15','-i','-','-an','-c:v','libx264','-preset','medium','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',str(result)],stdin=subprocess.PIPE)
count=0;shots=[];prev=None
pc_titles=['PC 端 · 多通道与设备状态','PC 端 · 四项曲线同屏查看','PC 端 · CAN 监控与单次发送']
pc_captions=['Linux / Windows · 独立通道标签 · 温度卡片 · 配置与 CSV 导出','角度 / 速度 / 电流 / 温度 · 动态曲线 · 点击放大','CAN 帧率与负载统计 · 标准 / 扩展帧 · 数据 / 远程帧']
def emit(im,pc=None):
 global count,prev
 d=ImageDraw.Draw(im)
 d.rectangle((0,0,1279,114),fill='#0c1320')
 d.text((48,26),'CAN Scope  /  安卓与 PC 联合功能介绍',font=fonts[40],fill='#eff5ff')
 d.text((48,82),pc_titles[pc]+'  ·  Desktop 1.0.0' if pc is not None else '安卓 App  ·  Android 1.20.0  ·  蓝牙 / Wi-Fi',font=fonts[20],fill='#94a6bc')
 d.rectangle((0,950,1279,999),fill='#0c1320')
 d.line((48,955,1232,955),fill='#26354a',width=2);d.line((48,955,48+int(1184*(count+1)/1200),955),fill='#65e6cb',width=3)
 d.text((48,966),'CAN Scope  ·  Android / Linux / Windows',font=fonts[18],fill='#92a3b8');d.text((1095,966),f'{count//15:02d} / 80 秒',font=fonts[18],fill='#92a3b8')
 write.stdin.write(im.tobytes())
 if count in [30,240,360,495,660,780,915,1050,1140]:
  shots.append(im.copy());im.save(OUT/f'联合分镜-{count//15:02d}.png')
 count+=1;prev=im
try:
 for frame in range(840):
  if frame in (180,480,600):
   part={180:0,480:1,600:2}[frame]
   for k in range(120):
    canvas=Image.new('RGB',(1280,1000),'#0c1320');d=ImageDraw.Draw(canvas)
    with Image.open(OUT/f'pc-frames/{part*120+k:04}.png') as screen:
     screen=screen.convert('RGB').resize((1120,781),Image.Resampling.LANCZOS)
     d.rounded_rectangle((72,120,1208,917),radius=12,fill='#253349');canvas.paste(screen,(80,128))
    caption=pc_captions[part]
    if part==2 and k>=45:caption='填写 CAN ID、帧格式、DLC 与数据字节 · 单次发送面板'
    d.text((80,923),caption,font=fonts[18],fill='#b5c5d9')
    emit(canvas,part)
   print('Inserted desktop section',part+1,flush=True)
  data=read.stdout.read(size)
  if len(data)!=size:raise RuntimeError('Incomplete source frame')
  emit(Image.frombytes('RGB',(1280,1000),data))
 assert not read.stdout.read(1);assert read.wait()==0
 write.stdin.close();assert write.wait()==0;assert count==1200
finally:
 for proc in (read,write):
  if proc.poll() is None:proc.terminate();proc.wait()
contact=Image.new('RGB',(1281,1002),'#0c1320')
for i,shot in enumerate(shots):contact.paste(shot.resize((427,334)),((i%3)*427,(i//3)*334))
contact.save(OUT/'安卓与PC联合功能分镜.png')
(OUT/'剪辑说明.json').write_text(json.dumps({'duration':80,'fps':15,'frames':count,'desktop_insertions':[[12,20],[40,48],[56,64]],'android_source':str(source),'desktop_capture':report},ensure_ascii=False,indent=2),encoding='utf8')
print(result)
