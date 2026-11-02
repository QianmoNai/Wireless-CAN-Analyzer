#!/usr/bin/env python3
"""Offline Android WebView showcase; real UI, generated telemetry, no hardware access."""
from pathlib import Path
import io,json,math,subprocess,base64
from PIL import Image,ImageDraw,ImageFont
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];ASSETS=ROOT/'android/app/src/main/assets'
OUT=ROOT/'dist/android-showcase';OUT.mkdir(parents=True,exist_ok=True)
FPS=15;DURATION=56
SCENES=[(0,'设备总览','多个通道，一屏掌握',['电机 / IMU 按通道汇总','右下角直接查看电机温度'],'点击卡片进入设备详情'),(5,'通道筛选','专注当前设备',['按通道筛选首页卡片','各设备保留独立配置'],'选择“通道 1”'),(8,'卡片排序','按使用习惯排列',['长按卡片并拖动调整顺序','松手后保存排列'],'长按 → 拖动 → 松手'),(12,'添加电机','型号与反馈 ID 独立配置',['支持 DJI、达妙电机和 IMU','填写名称、设备 ID、反馈 ID'],'选择型号，保存到当前通道'),(18,'电机详情','参数与曲线同步查看',['角度、速度、电流、温度','查看采样频率与设备状态'],'从首页进入单设备详情'),(22,'实时曲线','多项反馈连续变化',['纵向滑动查看各项曲线','点击任一曲线进入全屏'],'滑动查看 → 点击放大'),(27,'全屏曲线','横屏查看更多细节',['调整时间窗、暂停和清空','返回后继续查看设备详情'],'横屏全屏 · 速度曲线'),(32,'CAN 数据流','统计与原始报文同屏',['接收 / 发送帧率与总线负载','ID、DLC、数据字节逐帧查看'],'实时监控 CAN 总线'),(36,'报文筛选','快速定位目标 ID',['按 CAN ID 缩小查看范围','独立查看 RX、TX 和失败记录'],'输入 201 → 查看接收帧'),(40,'Wi-Fi 配网','通过蓝牙配置局域网',['扫描并选择附近 Wi-Fi','读取设备联网状态与 IP'],'蓝牙模式下完成配网'),(45,'通信选择','蓝牙 / Wi-Fi 自由选择',['保留原有蓝牙连接方式','局域网发现或手动 IP 连接'],'切换 Wi-Fi → 搜索设备'),(50,'配置备份','保存常用设备配置',['备份通道、电机与记录上限','导入配置，恢复常用工作环境'],'首页 → 配置备份与恢复')]
font={s:ImageFont.truetype('/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',s) for s in [18,20,24,30,40]}
channels=[]
for slot,name in [(1,'底盘 CAN'),(2,'机械臂 CAN')]:
 motors=[]
 for i in [1,2]:
  motors.append(dict(key=json.dumps(dict(profile='c620',id=i,feedbackId=512+i)),name=(['左轮电机','右轮电机'] if slot==1 else ['关节电机 1','关节电机 2'])[i-1],id=str(i),feedbackId=f'0x{512+i:X}',model='DJI C620 / M3508',connectionStatus='online',temperature=39+slot*2+i,temperatureAge=0,statusAge=0))
 channels.append(dict(slot=slot,name=name,connected=True,label='已连接',motors=motors))
INIT="""window.CanAndroid=new Proxy({}, {get:()=>()=>{}});window.HomeAndroid=new Proxy({}, {get:()=>()=>{}});"""
FEED="""t=>{const make=(kind,id,data)=>{const a=new Uint8Array(20);a[0]=1;a[1]=kind;a[3]=data.length;put32(a,4,id);a.set(data,8);return a};
 const angle=Math.round(4096+2200*Math.sin(t*2)),speed=Math.round(240+100*Math.sin(t*3));
 const bytes=[angle>>8,angle&255,speed>>8,speed&255,0,80,43,0];
 processPacket(hexOf(make(1,0x201,bytes)));processPacket(hexOf(make(1,0x202,[12,30,0,160,0,50,44,0])));
 if(detailMotor){for(let n=0;n<5;n++){let tt=t+n/75;let a=make(11,1001,bytes);let v=Math.round(4096+2200*Math.sin(tt*2));a[8]=v>>8;a[9]=v&255;let sp=Math.round(240+100*Math.sin(tt*3));a[10]=sp>>8;a[11]=sp&255;put32(a,16,Math.round(tt*1000));onNative('motorSample',{key:JSON.stringify(detailMotor.config),hex:hexOf(a),age:0})}}
 else {let a=make(14,700+Math.round(20*Math.sin(t)),[]);put32(a,8,100);put32(a,12,1000000);put32(a,16,1000000);onNative('busRates',{hex:hexOf(a)});a=make(13,80000,[]);a[2]=122;a[3]=4;put32(a,8,12000);put32(a,12,1000000);put32(a,16,1000000);onNative('busLoad',{hex:hexOf(a)});updateMonitor();renderFrames()}
}"""
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
 ctx=browser.new_context(viewport={'width':412,'height':800},device_scale_factor=1)
 ctx.route('https://showcase.local/**',lambda r:r.fulfill(path=str(ASSETS/r.request.url.rsplit('/',1)[-1])))
 ctx.add_init_script(INIT);errors=[]
 home=ctx.new_page();app=ctx.new_page()
 for page in [home,app]:page.on('pageerror',lambda e:errors.append(str(e)))
 home.goto('https://showcase.local/home.html');home.evaluate('updateHome',channels)
 app.goto('https://showcase.local/index.html');app.evaluate("onNative('state',{value:'ready',name:'底盘 CAN'});let cfg=new Uint8Array(20);cfg[0]=1;cfg[1]=5;cfg[3]=201;put32(cfg,4,1000000);put32(cfg,16,1000);processPacket(hexOf(cfg));commitWatch({profile:'c620',name:'左轮电机',id:1,feedbackId:513});")
 for page in [home,app]:page.wait_for_timeout(500)
 sessions={page:ctx.new_cdp_session(page) for page in [home,app]}
 movie=OUT/'CAN-Scope-安卓App功能介绍.mp4'
 encoder=subprocess.Popen(['ffmpeg','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','1280x1000','-r',str(FPS),'-i','-','-an','-c:v','libx264','-preset','medium','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',str(movie)],stdin=subprocess.PIPE)
 shots=[];active=home;landscape=False;drag=None
 try:
  for frame in range(FPS*DURATION):
   t=frame/FPS
   if frame%75==0:print('Rendering',t,flush=True)
   if frame==5*FPS:home.select_option('#filter','1')
   if frame==8*FPS:
    home.select_option('#filter','all');a=home.locator('.motor').nth(0).bounding_box();b=home.locator('.motor').nth(1).bounding_box();drag=(a,b);home.mouse.move(a['x']+40,a['y']+50);home.mouse.down()
   if 8.7<=t<9.7 and drag:
    a,b=drag;u=(t-8.7);home.mouse.move(a['x']+40+(b['x']-a['x'])*u,a['y']+50+(b['y']-a['y'])*u)
   if frame==10*FPS:home.mouse.up()
   if frame==12*FPS:
    active=app;app.locator('#rosterAdd').click();app.fill('#watchName','右轮电机');app.fill('#watchId','2');app.fill('#watchFeedback','202')
   if frame==17*FPS:app.locator('#watchSave').click()
   if frame==18*FPS:
    app.evaluate("dashboardOpen(JSON.stringify(watchedMotors[0].config));onNative('detailSupport',{supported:true,maxHz:1000,channel:'底盘 CAN'});onNative('telemetryMode',{mode:2,hz:100});")
   if 22<=t<24:app.evaluate('(y)=>window.scrollTo(0,y)',int((t-22)*230))
   if frame==27*FPS:
    app.evaluate("openDetailChart('speed')");app.set_viewport_size({'width':1000,'height':520});landscape=True
   if frame==32*FPS:
    app.evaluate("closeDetailChartFullscreen();showTab('live');onNative('telemetryMode',{mode:0});window.scrollTo(0,0)");app.set_viewport_size({'width':412,'height':800});landscape=False
   if frame==36*FPS:app.fill('#filter','201');app.evaluate("selectFrameFilter('rx');renderFrames()")
   if 37<=t<38:app.evaluate('(y)=>window.scrollTo(0,y)',int((t-37)*210))
   if frame==40*FPS:
    app.evaluate("showTab('settings');window.scrollTo(0,0);onNative('networkMode',{mode:'ble',paired:true});onNative('wifiScan',{rows:[['4C61622D57694669',-42,3],['43414E2D4C6162',-60,3]]});onNative('wifiStatus',{state:2,mode:0,ip:'192.168.1.123',ssid:'4C61622D57694669'});")
   if 42<=t<43:app.evaluate('(y)=>window.scrollTo(0,y)',int((t-42)*210))
   if frame==45*FPS:app.evaluate("window.scrollTo(0,0);onNative('networkMode',{mode:'wifi',paired:true,ip:'192.168.1.123'})")
   if frame==47*FPS:app.evaluate("$('deviceModal').hidden=false;scan();onNative('device',{name:'底盘 CAN',address:'192.168.1.123',transport:'wifi'});")
   if frame==50*FPS:active=home;home.evaluate("document.querySelector('.config-box').open=true;window.scrollTo(0,0)")
   home.evaluate('updateHome',channels);app.evaluate(FEED,t)
   active.wait_for_timeout(1000/FPS)
   scene=max((s for s in SCENES if s[0]<=t),key=lambda s:s[0]);start,title,subtitle,lines,action=scene
   canvas=Image.new('RGB',(1280,1000),'#0c1320');d=ImageDraw.Draw(canvas)
   d.text((48,26),'CAN Scope  /  安卓 App 功能介绍',font=font[40],fill='#eff5ff')
   d.text((48,82),'Android 1.20.0  ·  电机与 IMU  ·  蓝牙 / Wi-Fi',font=font[20],fill='#94a6bc')
   screen=Image.open(io.BytesIO(base64.b64decode(sessions[active].send('Page.captureScreenshot',{'format':'png'})['data']))).convert('RGB')
   if landscape:
    screen=screen.resize((1184,616),Image.Resampling.LANCZOS);x,y=48,160;tx,ty=48,818
   else:x,y=96,128;tx,ty=590,260
   d.rounded_rectangle((x-10,y-10,x+screen.width+10,y+screen.height+10),radius=22,fill='#253349');canvas.paste(screen,(x,y))
   if landscape:
    d.text((tx,ty),title+'  /  '+subtitle,font=font[30],fill='#65e6cb');d.text((tx,ty+55),'时间窗调整 · 暂停曲线 · 清空记录 · 返回详情',font=font[24],fill='#b5c5d9')
   else:
    n=next(i for i,s in enumerate(SCENES) if s is scene)+1
    d.text((tx,ty-70),f'{n:02d}  /  {len(SCENES):02d}',font=font[20],fill='#65e6cb')
    d.text((tx,ty),title,font=font[40],fill='#eff5ff');d.text((tx,ty+70),subtitle,font=font[30],fill='#65e6cb')
    for i,line in enumerate(lines):d.text((tx,ty+150+i*48),line,font=font[24],fill='#b5c5d9')
    d.line((tx,ty+285,tx+520,ty+285),fill='#26354a',width=2)
    d.text((tx,ty+315),action,font=font[24],fill='#94a6bc')
    for i,s in enumerate(SCENES):
     xx=tx+i*43;d.rounded_rectangle((xx,ty+410,xx+30,ty+414),radius=2,fill='#65e6cb' if s[0]<=t else '#26354a')
   d.line((48,955,1232,955),fill='#26354a',width=2);d.line((48,955,48+int(1184*(frame+1)/(FPS*DURATION)),955),fill='#65e6cb',width=3)
   d.text((48,966),'CAN Scope  ·  安卓界面与操作',font=font[18],fill='#92a3b8');d.text((1095,966),f'{int(t):02d} / 56 秒',font=font[18],fill='#92a3b8')
   encoder.stdin.write(canvas.tobytes())
   if frame in [2*FPS,6*FPS,9*FPS,14*FPS,20*FPS,24*FPS,29*FPS,34*FPS,38*FPS,43*FPS,48*FPS,52*FPS]:
    canvas.save(OUT/f'scene-{int(t):02d}.png');shots.append(canvas.copy());print('Captured',t,title,flush=True)
  encoder.stdin.close();assert encoder.wait()==0
  assert not errors,errors
 finally:
  browser.close()
  if encoder.poll() is None:encoder.terminate();encoder.wait()
 contact=Image.new('RGB',(1280,1000),'#0c1320')
 for i,im in enumerate(shots):contact.paste(im.resize((320,250)),((i%4)*320,(i//4)*333))
 contact.save(OUT/'安卓App功能分镜.png')
 (OUT/'制作说明.json').write_text(json.dumps(dict(duration=DURATION,fps=FPS,source='Android WebView assets',telemetry='generated offline',page_errors=errors,scenes=SCENES),ensure_ascii=False,indent=2),encoding='utf8')
 print(movie)
