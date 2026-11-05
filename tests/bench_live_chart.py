"""Offline live-chart JS/SVG benchmark; no CAN hardware or compositor timing."""
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
parser.add_argument('--detail-script', type=Path, help='isolated candidate or historical JS')
options = parser.parse_args()
script = options.detail_script or ASSETS/'motor_detail.js'
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=options.chromium, args=['--no-sandbox', '--disable-gpu'])
    context = browser.new_context(viewport={'width': 1920, 'height': 1080})
    def route(request):
        name = request.request.url.rsplit('/', 1)[-1]
        request.fulfill(path=str(script if name == 'motor_detail.js' else ASSETS/name))
    context.route('https://**/*', route)
    context.add_init_script('localStorage.clear();window.CanAndroid={dashboard(){},showDetail(){},setMonitorVisible(){},setChartFullscreen(){}}')
    rows = []
    for profile, expanded in [('dm', False), ('dm', True), ('imu', False)]:
        page = context.new_page(); errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('https://bench.local/index.html')
        # Resolve actual supported profiles rather than inventing a new schema.
        row = page.evaluate("""({family,expanded}) => {
            onNative('state',{value:'ready',name:'Synthetic draw bench'});
            const profile=Object.keys(Motors.profiles).find(k=>Motors.profiles[k].family===family);
            commitWatch({profile,id:family==='imu'?0x60:1,feedbackId:family==='imu'?0x60:0x11,master:0x11,mode:'mit',limits:Motors.profiles[profile].limits});
            const motor=watchedMotors.find(m=>m.config.profile===profile);
            dashboardOpen(JSON.stringify(motor.config));
            onNative('detailSupport',{supported:true,maxHz:1000});onNative('telemetryMode',{mode:2,hz:1000});
            cancelDetailPaint();requestDetailPaint=()=>{};
            detailSamples=Array.from({length:60000},(_,i)=>{
                const values={};for(let k=0;k<detailSeries.length;k++){
                    const s=detailSeries[k];
                    if(!s.imu||i%4===k%4)values[s.key]=i%997===0?NaN:Math.sin(i/(31+k))*100+k;
                    if(s.extra)values[s.extra]=i%991===0?NaN:Math.cos(i/47)*30+40;
                }
                return {t:i+(i>=30000?400:0),values};
            });
            detailSampleHead=0;detailClock=detailSamples.at(-1).t;detailLastArrival=0;detailAppliedHz=1000;
            $('detailWindow').value='60';for(const s of detailSeries)s.card.detailVisible=true;
            if(expanded)openDetailChart('temperature');
            for(let n=0;n<10;n++)drawDetail();
            const times=[];for(let n=0;n<40;n++){const t=performance.now();drawDetail();times.push(performance.now()-t)}
            times.sort((a,b)=>a-b);
            const roots=expanded?[$('detailChartFullscreen')]:detailSeries.map(s=>s.card);
            const geometry=roots.map(root=>({paths:[...root.querySelectorAll('.trace,.trace-extra')].map(p=>p.getAttribute('d')),labels:[...root.querySelectorAll('.detail-scale span,.detail-axis span')].map(p=>p.textContent)}));
            return {family,expanded,samples:detailSamples.length,series:detailSeries.length,iterations:times.length,total_ms:times.reduce((a,b)=>a+b,0),p95_ms:times[Math.floor(times.length*.95)],geometry};
        }""", dict(family=profile, expanded=expanded))
        assert row['samples'] == 60000 and row['series'] >= 4 and not errors
        assert any(len(p or '') > 1000 for item in row['geometry'] for p in item['paths'])
        geometry = json.dumps(row.pop('geometry'), separators=(',', ':'), ensure_ascii=False)
        row.update(geometry_sha256=hashlib.sha256(geometry.encode()).hexdigest(), errors=errors)
        rows.append(row); page.close()
    browser.close()
report = dict(scope='synthetic Chromium JS/SVG draw only; no hardware or compositor timing',
              source_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
              index_sha256=hashlib.sha256((ASSETS/'index.html').read_bytes()).hexdigest(), cases=rows)
options.output.parent.mkdir(parents=True, exist_ok=True)
options.output.write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report))
