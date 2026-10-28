"""Reuse the measured native trial acquisition; no DB/source access here."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile

def need(ok, code):
    if not ok: raise RuntimeError(code)
def sha(raw): return hashlib.sha256(raw).hexdigest()
def encode(value): return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def put(path,raw):
    path=Path(path); path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    path.write_bytes(raw); path.chmod(0o600)

def safe_env(home):
    return {'PATH':'/usr/bin:/bin','HOME':str(home),'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1',
            'PIP_CONFIG_FILE':'/dev/null','PIP_DISABLE_PIP_VERSION_CHECK':'1','HF_HUB_DISABLE_IMPLICIT_TOKEN':'1'}


def native_integrity(runtime):
    runtime=Path(runtime)
    wheel=next((runtime/'wheels').glob('gliner2-2.1.0-*.whl'))
    site=runtime/'venv/lib/python3.13/site-packages';pins={}
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.startswith('gliner2/') and name.endswith('.py'):
                actual=site/name
                need(actual.is_file() and sha(actual.read_bytes())==sha(archive.read(name)),'NATIVE_CODE_CHANGED')
                pins[name]=sha(actual.read_bytes())
    need(len(pins)==91,'NATIVE_SOURCE_COUNT')
    return pins


def lock_valid(lock):
    need(lock.get('platform')=='ubuntu-24.04-x86_64' and lock.get('python')=='3.13','LOCK_TARGET')
    packages=lock['packages'];models=lock['model']
    need(0<len(packages)<=80 and len({p['name'].lower() for p in packages})==len(packages),'LOCK_PACKAGES')
    need(any(p['name']=='torch' and p['version']=='2.14.1+cpu' for p in packages),'CPU_TORCH_REQUIRED')
    need(not any(any(s in p['name'].lower() for s in ['cuda','nvidia','triton']) for p in packages),'NON_CPU_LOCK')
    need({m['path'] for m in models}=={'config.json','encoder_config/config.json','tokenizer_config.json','tokenizer.json','model.safetensors'},'MODEL_ALLOWLIST')
    need(lock['model_revision']=='cf5593a5d45e3bbf204b9df621b13b1c0cf25ee3','MODEL_REVISION')
    hosts={'files.pythonhosted.org','download.pytorch.org','download-r2.pytorch.org','huggingface.co'}
    for item in packages+models:
        need(type(item['bytes']) is int and item['bytes']>0 and re.fullmatch('[a-f0-9]{64}',item['sha256']),'LOCK_PIN')
        parsed=urllib.parse.urlparse(item['url'])
        need(parsed.scheme=='https' and parsed.hostname in hosts and not parsed.username and not parsed.password,'LOCK_URL')
        name=item.get('filename',item.get('path'))
        need('..' not in Path(name).parts and not Path(name).is_absolute(),'LOCK_PATH')
        if item in packages:need(name.endswith('.whl') and '/' not in name,'WHEEL_ONLY')
    size=sum(v['bytes'] for v in packages+models)
    need(size==lock['payload_bytes'] and size<=2*1024**3,'LOCK_DOWNLOAD_CAP')
    return True


def acquire(lock,runtime,fetch=urllib.request.urlopen,clock=time.monotonic):
    lock_valid(lock);runtime=Path(runtime);began=clock();total=0
    ledger={'downloads':[],'downloaded_bytes':0}
    for item in lock['packages']+lock['model']:
        need(clock()-began<1200,'ACQUISITION_TIMEOUT')
        relative='wheels/'+item['filename'] if 'filename' in item else 'model/'+item['path']
        dest=runtime/relative;dest.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        digest=hashlib.sha256();size=0
        request=urllib.request.Request(item['url'],headers={'User-Agent':'BOHUN-frozen-trial/1'})
        with fetch(request,timeout=min(45,1200-(clock()-began))) as response,dest.open('xb') as out:
            while chunk:=response.read(1024**2):
                size+=len(chunk);total+=len(chunk)
                need(size<=item['bytes'] and total<=2*1024**3,'DOWNLOAD_SIZE_CAP')
                need(clock()-began<1200,'ACQUISITION_TIMEOUT')
                out.write(chunk);digest.update(chunk)
        dest.chmod(0o600)
        need(size==item['bytes'] and digest.hexdigest()==item['sha256'],'DOWNLOAD_PIN')
        ledger['downloads'].append({'path':relative,'bytes':size,'sha256':digest.hexdigest()})
        ledger['downloaded_bytes']=total
        (runtime/'acquisition.json').write_bytes(encode(ledger))
    env=safe_env(runtime/'home')
    (runtime/'home').mkdir(mode=0o700,exist_ok=True)
    left=lambda:1200-(clock()-began)
    need(left()>0,'ACQUISITION_TIMEOUT')
    expanded=0
    for item in lock['packages']:
        with zipfile.ZipFile(runtime/'wheels'/item['filename']) as archive:
            expanded+=sum(i.file_size for i in archive.infolist())
    # Include existing payload + fully expanded wheel contents + conservative venv/log reserve.
    need(total+expanded+256*1024**2<=4*1024**3,'PROJECTED_DISK_CAP')
    with (runtime/'install.log').open('w') as log:
        subprocess.run([sys.executable,'-m','venv',str(runtime/'venv')],env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=left())
        req='\n'.join(f'{p["name"]}=={p["version"]} --hash=sha256:{p["sha256"]}' for p in lock['packages'])+'\n'
        put(runtime/'requirements.lock',req.encode())
        need(left()>0,'ACQUISITION_TIMEOUT')
        subprocess.run([str(runtime/'venv/bin/python'),'-m','pip','install','--no-index','--no-deps','--no-compile',
                       '--find-links',str(runtime/'wheels'),'--require-hashes','-r',str(runtime/'requirements.lock')],
                       env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=left())
    need(sum(f.stat().st_size for f in runtime.rglob('*') if f.is_file())<=4*1024**3,'TRIAL_DISK_CAP')
    put(runtime/'native-before.json',encode(native_integrity(runtime)))
    return ledger



def main():
    import os
    import platform
    import shutil
    os.umask(0o077)
    need(sys.platform.startswith("linux") and sys.version_info[:2]==(3,13) and platform.machine()=="x86_64","PINNED_PLATFORM")
    root=Path(os.environ["RUNNER_TEMP"])/"bohun-company-ner"
    need(not root.exists(),"RUNTIME_ALREADY_EXISTS")
    root.mkdir(mode=0o700)
    need(shutil.disk_usage(root).free>=6*1024**3,"FREE_DISK_PREFLIGHT")
    lock=json.loads(Path(__file__).with_name("acquisition-lock.json").read_bytes())
    ledger=acquire(lock,root)
    # The native environment is reused for the parent driver too. These are the
    # same PostgreSQL driver versions used in the successful Inoreader pilot.
    subprocess.run([str(root/"venv/bin/python"),"-m","pip","install","--only-binary=:all:",
        "--no-deps","psycopg==3.3.6","psycopg-binary==3.3.6"],
        env=safe_env(root/"home"),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=120)
    need(native_integrity(root)==json.loads((root/"native-before.json").read_bytes()),"NATIVE_CHANGED_AFTER_DRIVER")
    print(json.dumps({"acquisition_bytes":ledger["downloaded_bytes"],"native_files_verified":91}))

if __name__=="__main__":
    try: main()
    except Exception: raise SystemExit("COMPANY_NER_ACQUISITION_STOP_NO_RETRY") from None
