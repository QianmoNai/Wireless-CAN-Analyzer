"""Synthetic paused-chart CPU benchmark; not hardware or compositor timing."""
import argparse
import hashlib
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT/'android/app/src/main/assets'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--chromium')
options = parser.parse_args()
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=options.chromium, args=['--no-sandbox', '--disable-gpu'])
    context = browser.new_context(viewport={'width': 1920, 'height': 1080})
    context.route('https://**/*', lambda route: route.fulfill(path=str(ASSETS/route.request.url.rsplit('/', 1)[-1])))
    context.add_init_script('window.CanAndroid={dashboard(){},showDetail(){},setMonitorVisible(){},setChartFullscreen(){}}')
    page = context.new_page(); errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('https://bench.local/index.html')
    report = page.evaluate("""() => {
        onNative('state',{value:'ready',name:'Synthetic bench'});
        commitWatch({profile:'c620',id:1});dashboardOpen(JSON.stringify(watchedMotors[0].config));
        onNative('detailSupport',{supported:true,maxHz:1000});onNative('telemetryMode',{mode:2,hz:1000});
        detailSamples=Array.from({length:60000},(_,i)=>({t:i,values:{position:Math.sin(i/300)*1000,speed:i%1000,effort:Math.cos(i/500),temperature:40}}));
        detailSampleHead=0;detailClock=59999;detailLastArrival=Date.now();detailAppliedHz=1000;
        $('detailWindow').value='60';detailTogglePause();openDetailChart('position');cancelDetailPaint();
        for(let i=0;i<5;i++)drawDetail();
        const path=$('detailChartSvg').querySelector('.trace').getAttribute('d'),times=[],batches=[];
        for(let b=0;b<3;b++){const began=performance.now();for(let i=0;i<50;i++){const t=performance.now();drawDetail();times.push(performance.now()-t)}batches.push(performance.now()-began)}
        times.sort((a,b)=>a-b);
        return {samples:detailSamples.length,frozen_samples:detailFrozenSamples.length,
            visible_samples:detailFrozenSamples.filter(p=>p.t>=detailFrozen-60000&&p.t<=detailFrozen).length,path_length:path.length,
            unchanged_path:$('detailChartSvg').querySelector('.trace').getAttribute('d')===path,
            batches_ms:batches,total_ms:batches.reduce((a,b)=>a+b,0),p95_ms:times[Math.floor(times.length*.95)],iterations:times.length};
    }""")
    report.update(scope='synthetic Chromium JS/SVG callback only; no CAN hardware or compositor timing', source_sha256=hashlib.sha256((ASSETS/'motor_detail.js').read_bytes()).hexdigest(), index_sha256=hashlib.sha256((ASSETS/'index.html').read_bytes()).hexdigest(), errors=errors)
    assert report['samples'] == 60000 and report['frozen_samples'] == 60000 and report['visible_samples'] >= 59000 and report['path_length'] > 1000 and report['unchanged_path'] and not errors, report
    browser.close()
options.output.parent.mkdir(parents=True, exist_ok=True)
options.output.write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report))
