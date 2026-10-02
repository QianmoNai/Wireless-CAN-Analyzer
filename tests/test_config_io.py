"""Backup schema and channel storage round trips; Android document picker is mocked."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];ASSETS=ROOT/'android/app/src/main/assets'
with sync_playwright() as p:
 b=p.chromium.launch(args=['--no-sandbox'])
 ctx=b.new_context(viewport={'width':412,'height':915},device_scale_factor=2)
 ctx.route('https://**/*',lambda route:route.fulfill(path=str(ASSETS/route.request.url.rsplit('/',1)[-1])))
 ctx.add_init_script('window.sent=[];window.actions=[];window.CanAndroid={scan(){},connect(){},disconnect(){},send(h){sent.push(h)}};window.HomeAndroid={exportConfig(){actions.push("export")},importConfig(){actions.push("import")}}')
 pages=[];errors=[]
 for slot in (1,2):
  page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto('https://'+('app' if slot==1 else 'channel-2')+'.local/index.html');pages.append(page)
  page.evaluate("slot=>{commitWatch({profile:'c620',name:'轮子 '+slot,id:slot,feedbackId:0x310+slot});commitWatch({profile:'dm4310',name:'关节',id:slot,master:0x120,feedbackId:0x120,mode:'velocity',limits:{p:12.5,v:30,t:10}});$('rxLimit').value=slot*100;$('txLimit').value=slot*200;applyHistoryLimits()}",slot)
 pages[0].evaluate("ConfigIO.writeField('detailRates',{'c620:1:785':1000,'dm4310:1:288':25})")
 # Native passes the confirmed device separately from the last attempted device.
 histories=[{'address':'aa:bb:cc:dd:ee:01','name':'底盘 · 成功设备'}, {'address':'AA:BB:CC:DD:EE:02','name':'关节 · 成功设备'}]
 channels=[page.evaluate("data=>ConfigIO.exportChannel(data.slot,'最近尝试-'+data.slot,'8C:4F:00:A9:33:BC',data.history)",{'slot':slot,'history':histories[slot-1]}) for slot,page in enumerate(pages,1)]
 for c,h in zip(channels,histories):
  assert c['lastSuccessfulDevice']==dict(h,address=h['address'].upper())
  assert c['lastSuccessfulDevice']['address']!=c['address']
  assert c['lastSuccessfulDevice']['name']!=c['name']
 doc={'format':'can-scope-config','version':1,'channels':channels}
 home=ctx.new_page();home.on('pageerror',lambda e:errors.append(str(e)));home.goto('https://home.local/home.html')
 checked=home.evaluate('text=>validateImport(text)',json.dumps(doc,ensure_ascii=False));assert checked['ok']
 assert checked['data']['channels']==channels
 # A second export after restore retains the native record without making it live.
 exported_again=pages[0].evaluate("c=>ConfigIO.exportChannel(c.slot,c.name,c.address,c.lastSuccessfulDevice)",checked['data']['channels'][0])
 assert exported_again==channels[0]
 # Older files and channels without success history remain valid; ordinary addresses are not promoted.
 legacy_channel=pages[0].evaluate("ConfigIO.exportChannel(1,'旧设备','8C:4F:00:A9:33:BC')")
 assert 'lastSuccessfulDevice' not in legacy_channel
 legacy_doc={'format':'can-scope-config','version':1,'channels':[legacy_channel]}
 old_checked=home.evaluate('text=>validateImport(text)',json.dumps(legacy_doc));assert old_checked['ok']
 assert 'lastSuccessfulDevice' not in old_checked['data']['channels'][0]
 mixed=json.loads(json.dumps(doc));mixed['channels'][1].pop('lastSuccessfulDevice')
 mixed_checked=home.evaluate('text=>validateImport(text)',json.dumps(mixed));assert mixed_checked['ok']
 assert mixed_checked['data']['channels'][0]['lastSuccessfulDevice']==channels[0]['lastSuccessfulDevice']
 assert 'lastSuccessfulDevice' not in mixed_checked['data']['channels'][1]
 home.locator('.config-box summary').click();home.locator('#exportConfig').click();home.locator('#importConfig').click();assert home.evaluate('actions')==['export','import']
 home.evaluate("configStatus('文件检查通过：2 个通道、4 台电机',false)")
 home.locator('.config-box').screenshot(path=str(ROOT/'android/dist/preview-config-backup.png'))
 # A fresh origin gets exactly the saved settings; import is one localStorage commit.
 target=ctx.new_page();target.goto('https://channel-3.local/index.html')
 result=target.evaluate('c=>ConfigIO.install(c,"revision-1")',channels[0]);assert result=={'ok':True,'reload':True}
 target.reload();assert target.evaluate('watchedMotors.map(m=>m.config)')==channels[0]['motors']
 assert target.evaluate('[rxSaveLimit,txSaveLimit]')==[100,200]
 assert target.evaluate('detailRateFor(watchedMotors[0])')==1000
 assert target.evaluate('detailRateFor(watchedMotors[1])')==25
 assert target.evaluate('frames.length')==0 and target.evaluate('sent.length')==0
 assert not target.evaluate('connected')
 # Replaying a committed restore after restart must not overwrite later user edits.
 target.evaluate("$('rxLimit').value=987;applyHistoryLimits()")
 assert target.evaluate('c=>ConfigIO.install(c,"revision-1")',channels[0])=={'ok':True,'reload':False}
 target.reload();assert target.evaluate('rxSaveLimit')==987
 assert pages[1].evaluate('rxSaveLimit')==200,'Other channel origin changed'
 # New import revision applies again. Injected runtime/command properties are discarded.
 malicious=json.loads(json.dumps(doc));malicious['channels'][0]['motors'][0]['command']={'current':16384};malicious['channels'][0]['connected']=True
 malicious['channels'][0]['lastSuccessfulDevice'].update(connected=True,polling=True,command={'current':16384})
 clean=home.evaluate('text=>validateImport(text)',json.dumps(malicious));assert clean['ok']
 assert 'command' not in clean['data']['channels'][0]['motors'][0] and 'connected' not in clean['data']['channels'][0]
 assert clean['data']['channels'][0]['lastSuccessfulDevice']==channels[0]['lastSuccessfulDevice']
 assert home.evaluate('text=>validateImport(text)', '\ufeff'+json.dumps(doc))['ok']
 bad=[]
 for alter in [lambda d:d['channels'][0]['detailRates'].update({'c620:1:785':1001}),lambda d:d['channels'][0]['detailRates'].update({'c620:1:785':0}),lambda d:d['channels'][0]['motors'][1].update(pollHz=1001),lambda d:d['channels'][0]['motors'][1].update(pollHz=0),lambda d:d['channels'][0]['motors'][1].update(pollHz=1.5),lambda d:d.update(version=99),lambda d:d.update(format='random'),lambda d:d['channels'].append(d['channels'][0]),lambda d:d['channels'][0]['historyLimits'].update(rx=-1),lambda d:d['channels'][0]['motors'][0].update(profile='__proto__'),lambda d:d['channels'][0]['motors'][1].update(feedbackId=0x311,master=0x311),lambda d:d['channels'][0]['motors'][1]['limits'].update(v=0),lambda d:d['channels'][0]['motors'][0].update(id=1.5),lambda d:d['channels'][0].update(address='bad'),lambda d:d['channels'][0]['motors'][1].update(mode='bad')]:
  d=json.loads(json.dumps(doc));alter(d);bad.append(d)
 for record in [None,[],True,'AA:BB:CC:DD:EE:01',{}, {'address':'AA:BB:CC:DD:EE:01'}, {'name':'缺地址'}, {'address':'','name':'空地址'}, {'address':'bad','name':'坏地址'}, {'address':'AA:BB:CC:DD:EE:01','name':'x'*65}, {'address':'AA:BB:CC:DD:EE:01','name':'bad\nname'}, {'address':'AA:BB:CC:DD:EE:01','name':1}]:
  d=json.loads(json.dumps(doc));d['channels'][0]['lastSuccessfulDevice']=record;bad.append(d)
 before=target.evaluate("localStorage.getItem('canScopeChannelV1')")
 for d in bad:assert not home.evaluate('text=>validateImport(text)',json.dumps(d))['ok']
 assert not home.evaluate('text=>validateImport(text)','{"broken":')['ok']
 assert not home.evaluate('text=>validateImport(text)','x'*1048577)['ok']
 assert target.evaluate("localStorage.getItem('canScopeChannelV1')")==before
 # Storage failure cannot half-replace the roster and history caps.
 target.evaluate('() => {window.realSet=Storage.prototype.setItem;Storage.prototype.setItem=function(){throw Error("storage full")}}')
 failure=target.evaluate('c=>ConfigIO.install(c,"revision-2")',channels[1]);assert not failure['ok']
 target.evaluate('() => {Storage.prototype.setItem=window.realSet}')
 assert target.evaluate("localStorage.getItem('canScopeChannelV1')")==before
 assert target.evaluate('c=>ConfigIO.install(c,"revision-2")',channels[1])['reload']
 target.reload();assert target.evaluate('watchedMotors.map(m=>m.config)')==channels[1]['motors']
 # Migration: old per-key configuration stays readable and is folded into the atomic record on edit.
 legacy=ctx.new_page();legacy.goto('https://channel-4.local/index.html')
 legacy.evaluate("c=>{localStorage.setItem('watchedMotors',JSON.stringify(c.motors));localStorage.setItem('historyLimits',JSON.stringify(c.historyLimits))}",channels[0]);legacy.reload()
 assert legacy.evaluate('rxSaveLimit')==100
 legacy.evaluate("$('txLimit').value=0;applyHistoryLimits()")
 legacy.reload();assert legacy.evaluate('txSaveLimit')==0 and legacy.evaluate('watchedMotors.length')==2
 assert not errors,errors
 b.close()
print('PASS: successful-device backup round trip, per-channel history and attempted-address isolation, absent-record compatibility, malformed-history rejection, origin isolation, revision replay, schema/version/conflict/range validation, corrupt/oversize rejection, atomic storage failure, legacy migration, no CAN sends.')
