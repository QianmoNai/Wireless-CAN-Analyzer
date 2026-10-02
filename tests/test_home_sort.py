from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];ASSETS=ROOT/'android/app/src/main/assets'
with sync_playwright() as p:
 b=p.chromium.launch(args=['--no-sandbox'])
 ctx=b.new_context(viewport={'width':412,'height':915},has_touch=True)
 ctx.route('https://**/*',lambda route:route.fulfill(path=str(ASSETS/route.request.url.rsplit('/',1)[-1])))
 ctx.add_init_script('window.actions=[];window.HomeAndroid={openMotor(...a){actions.push(a)},deleteMotor(...a){actions.push(["delete",...a])},editMotor(...a){actions.push(["edit",...a])}}')
 page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 data=[dict(slot=i,name='设备'+str(i),connected=True,label='已连接',motors=[dict(key='{"profile":"c620","id":'+str(j)+',"feedbackId":'+str(512+j)+'}',name=f'M{i}{j}',id=str(j),feedbackId=str(512+j),model='C620') for j in [1,2]]) for i in [1,2]]
 page.goto('https://home.local/home.html');page.evaluate('updateHome',data)
 def names():return page.locator('.motor h2').all_text_contents()
 assert names()==['M11','M12','M21','M22']
 assert page.locator('#sortCards').count()==0
 a=page.locator('.motor').first.bounding_box();z=page.locator('.motor').nth(2).bounding_box()
 page.mouse.move(a['x']+30,a['y']+30);page.mouse.down();page.wait_for_timeout(650)
 assert page.locator('.drag-preview').count()==1
 beforeGhost=page.locator('.drag-preview').bounding_box()
 page.mouse.move(a['x']+38,a['y']+39);page.wait_for_timeout(50)
 afterGhost=page.locator('.drag-preview').bounding_box()
 assert abs(afterGhost['x']-beforeGhost['x']-8)<2 and abs(afterGhost['y']-beforeGhost['y']-9)<2
 page.mouse.move(z['x']+30,z['y']+30,steps=8);page.wait_for_timeout(1100)
 page.screenshot(path=str(ROOT/'android/dist/preview-drag.png'))
 page.mouse.up();page.wait_for_timeout(250);assert page.locator('.drag-preview').count()==0

 assert names()==['M12','M21','M11','M22'],names()
 assert page.evaluate('actions.length')==0
 page.reload();page.evaluate('updateHome',data);assert names()==['M12','M21','M11','M22']
 page.select_option('#filter','1');page.locator('.motor').first.focus();page.keyboard.press('Alt+ArrowRight')
 assert names()==['M11','M12']
 page.select_option('#filter','all');assert names()==['M11','M21','M12','M22']
 # Cancellation restores saved order, and live refresh cannot jump a dragging card.
 a=page.locator('.motor').first.bounding_box();z=page.locator('.motor').nth(1).bounding_box()
 page.mouse.move(a['x']+30,a['y']+30);page.mouse.down();page.wait_for_timeout(650);page.mouse.move(z['x']+30,z['y']+30)
 page.evaluate('endGesture(false)');page.mouse.up();assert names()==['M11','M21','M12','M22']
 page.locator('.motor').first.click();assert page.evaluate('actions.length')==1
 for width in [320,375,412,820]:
  page.set_viewport_size({'width':width,'height':915});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth,bad:[...document.querySelectorAll("body *")].filter(e=>e.getBoundingClientRect().right>innerWidth).map(e=>[e.className,e.getBoundingClientRect().right])})')
 # Real touch events use the same drag path without invoking long-press deletion.
 page.set_viewport_size({'width':412,'height':915})
 before=names();a=page.locator('.motor').first.bounding_box();z=page.locator('.motor').nth(1).bounding_box();cdp=ctx.new_cdp_session(page)
 cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':a['x']+30,'y':a['y']+30}]})
 page.wait_for_timeout(650)
 cdp.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':z['x']+30,'y':z['y']+30}]})
 page.wait_for_timeout(650)
 cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
 assert names()==[before[1],before[0],*before[2:]],names()
 assert page.evaluate('actions.length')==1
 # Touch swipes route to the exact channel/key; vertical scrolling remains native.
 def swipe(dx,dy):
  box=page.locator('.motor').first.bounding_box();x=box['x']+box['width']/2;y=box['y']+40
  cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x,'y':y}]})
  for i in range(1,6):cdp.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x+dx*i/5,'y':y+dy*i/5}]})
  cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
 page.evaluate('window.scrollTo(0,0)');swipe(70,0);assert page.evaluate('actions.at(-1)[0]')=='edit'
 before=page.evaluate('actions.length');swipe(-70,0);assert page.evaluate('actions.length')==before+1;assert page.evaluate('actions.at(-1)[0]')=='delete'
 before=page.evaluate('actions.length');swipe(0,-70);page.wait_for_timeout(700);assert page.evaluate('actions.length')==before
 # Reduced-motion mode preserves direct following and cleans up immediately.
 page.emulate_media(reduced_motion='reduce');page.evaluate('window.scrollTo(0,0)');box=page.locator('.motor').first.bounding_box()
 page.mouse.move(box['x']+30,box['y']+30);page.mouse.down();page.wait_for_timeout(600)
 assert page.locator('.drag-preview').count()==1
 page.evaluate('endGesture(false)');page.mouse.up();assert page.locator('.drag-preview').count()==0
 assert page.locator('.motor.dragging').count()==0
 assert not errors,errors
 b.close()
print('PASS: drag, persisted reload, filtered reorder, cancellation, no accidental open/delete, narrow layout')
