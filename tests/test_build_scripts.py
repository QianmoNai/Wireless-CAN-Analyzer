"""Black-box build-script checks with fake tools; no signing key or SDK changes."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='can-build-check-') as temp:
    base=Path(temp);project=base/'中文工程';project.mkdir()
    firmware=project/'firmware';firmware.mkdir();shutil.copy(ROOT/'firmware/idf.sh',firmware/'idf.sh')
    tools=base/'tools';tools.mkdir()
    idf=tools/'idf.py';idf.write_text('#!/usr/bin/env python3\nimport json,os,sys\nopen(os.environ["CAN_TEST_RECORD"],"w").write(json.dumps(sys.argv[1:]))\n');idf.chmod(0o755)
    record=base/'record.json';env={**os.environ,'PATH':str(tools)+os.pathsep+os.environ['PATH'],'XDG_CACHE_HOME':str(base/'cache'),'CAN_TEST_RECORD':str(record)}
    for key in ['CAN_IDF_BUILD_DIR','CAN_IDF_TARGET','CAN_ANDROID_BUILD_DIR']:
        env.pop(key,None)
    (firmware/'build').mkdir();legacy=firmware/'build/esp32s3.sdkconfig';legacy.write_text('CONFIG_SAVED_SETTING=42\n')
    def idf_run():return subprocess.run(['bash',str(firmware/'idf.sh'),'build'],env=env,capture_output=True,text=True)
    assert idf_run().returncode==0
    args=json.loads(record.read_text());build=Path(args[args.index('-B')+1]);assert str(build).isascii()
    alias=base/'alias';alias.symlink_to(project,target_is_directory=True)
    assert subprocess.run(['bash',str(alias/'firmware/idf.sh'),'build'],env=env,capture_output=True).returncode==0
    alias_args=json.loads(record.read_text());assert alias_args[alias_args.index('-B')+1]==str(build)
    saved=Path(str(build)+'.sdkconfig');assert saved.read_text()==legacy.read_text()
    legacy.write_text('CONFIG_SAVED_SETTING=99\n');assert idf_run().returncode==0;assert '42' in saved.read_text()
    with Path(str(build)+'.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        shutil.rmtree(build)  # fullclean must not delete the lock's inode.
        assert idf_run().returncode!=0
    env['XDG_CACHE_HOME']=str(base/'中文缓存');assert idf_run().returncode==0
    args=json.loads(record.read_text());fallback=Path(args[args.index('-B')+1]);assert str(fallback).isascii()
    # Remove only this disposable project's fallback cache, retaining shared cache roots.
    shutil.rmtree(fallback.parent);Path(str(fallback)+'.sdkconfig').unlink(missing_ok=True)

    android=project/'android';android.mkdir();shutil.copy(ROOT/'android/build.sh',android/'build.sh')
    java=android/'app/src/main/java';java.mkdir(parents=True);(java/'Example.java').write_text('class Example {}')
    sdk=base/'sdk';sdktools=sdk/'build-tools/35.0.0';sdktools.mkdir(parents=True)
    fake=tools/'fake-tool';fake.write_text('''#!/usr/bin/env python3
import os,sys,shutil,zipfile
from pathlib import Path
name=Path(sys.argv[0]).name;a=sys.argv[1:]
def value(flag):return a[a.index(flag)+1]
if name=='aapt2':
    with zipfile.ZipFile(value('-o'),'w'):pass
elif name=='javac':Path(value('-d'),'Fresh.class').write_bytes(b'fresh')
elif name=='jar':
    with zipfile.ZipFile(a[1],'w'):pass
elif name=='d8':Path(value('--output'),'classes.dex').write_bytes(b'fresh-dex')
elif name=='zipalign':shutil.copyfile(a[-2],a[-1])
elif name=='apksigner':
    if a[0]=='verify' and os.environ.get('CAN_TEST_BAD_SIGNATURE'):sys.exit(1)
    if a[0]=='sign':shutil.copyfile(a[-1],value('--out'))
''');fake.chmod(0o755)
    for name in ['javac','jar']:(tools/name).symlink_to(fake)
    for name in ['aapt2','d8','zipalign','apksigner']:(sdktools/name).symlink_to(fake)
    build=base/'android-build';(build/'classes').mkdir(parents=True);(build/'dex').mkdir()
    (build/'classes/Stale.class').write_bytes(b'stale');(build/'dex/classes2.dex').write_bytes(b'stale')
    env.update(ANDROID_HOME=str(sdk),CAN_ANDROID_BUILD_DIR=str(build))
    def android_run():return subprocess.run(['bash',str(android/'build.sh')],env=env,capture_output=True,text=True)
    result=android_run();assert result.returncode==0,result.stderr
    assert not (build/'classes/Stale.class').exists() and not (build/'dex/classes2.dex').exists()
    apk=android/'dist/CAN-Scope-debug.apk';before=apk.read_bytes();assert before
    env['CAN_TEST_BAD_SIGNATURE']='1';assert android_run().returncode!=0;assert apk.read_bytes()==before
    with (build/'build.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);assert android_run().returncode!=0
print('PASS: ASCII firmware cache, saved sdkconfig migration, concurrent build exclusion, stale class/DEX removal and publish only after signature verification.')
