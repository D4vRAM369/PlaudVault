"""Ensayo nativo con vault y audio ficticios; jamás usar config del usuario."""
import json
import re
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from make_gui_fixture import build
from plaudvault.config import load_config
from plaudvault.crypto import encrypt_bytes, unlock
from plaudvault.desktop import runtime_environment, webkit_sandbox_problem
from plaudvault.gui import create_app, kill_group
import uvicorn
from fastapi import Request

work = Path(tempfile.mkdtemp(prefix='pv-native-integration-', dir='/dev/shm'))
results = []
tracked=[]
servers=[]
apps=[]
script = r'''(async()=>{
const wait=async(fn)=>{for(let i=0;i<200;i++){if(fn())return;await new Promise(r=>setTimeout(r,100));}throw Error('timeout');};
try{
 await wait(()=>document.querySelectorAll('.rec').length>=3);
 const record=[...document.querySelectorAll('.rec')].find(e=>e.textContent.includes('Reunión'));
 record.click();await wait(()=>document.querySelector('.document')?.textContent.includes('presupuesto'));
 const heading=document.querySelector('h1').textContent;
 document.querySelector('.lock').click();await wait(()=>document.querySelector('#passphrase'));
 const input=document.querySelector('#passphrase');input.value='test';input.dispatchEvent(new Event('input',{bubbles:true}));
 await wait(()=>!document.querySelector('.unlock-panel .primary').disabled);
 document.querySelector('.unlock-panel .primary').click();await wait(()=>!document.querySelector('.unlock-panel'));
 document.querySelector('.player .primary').click();await wait(()=>document.querySelector('audio'));
 const audio=document.querySelector('audio');audio.muted=true;await audio.play();await wait(()=>audio.currentTime>.4);
 const initial=audio.currentTime;audio.currentTime=audio.duration*.55;await wait(()=>audio.currentTime>initial+1&&!audio.seeking);
 await fetch('/__probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ok:true,records:document.querySelectorAll('.rec').length,document:!!heading,readyState:audio.readyState,currentTime:audio.currentTime,duration:audio.duration,path:location.pathname,queryEmpty:!location.search})});
}catch(e){await fetch('/__probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ok:false,error:String(e),page:document.body.textContent.slice(0,200)})});}
})();'''
try:
 fixture=work/'vault';build(fixture,minutes=.1)
 cfg=load_config(fixture/'config.yaml')
 # MP3 grande, generado y cifrado sin escribir el claro sobre un disco persistente.
 mp3=work/'synthetic.mp3'
 subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','sine=frequency=440:duration=960','-b:a','320k','-write_xing','0',str(mp3)],check=True)
 payload=mp3.read_bytes();mp3.unlink();key=unlock(cfg.salt_path,cfg.keycheck_path,'test')
 folder=next(p.parent for p in cfg.paths.archive.rglob('.plaud_id') if 'Reunión' in str(p))
 (folder/'audio.pv').write_bytes(encrypt_bytes(key,payload));chunk=payload[100000:104096]
 # Generar un binario de prueba a partir del binario real; único añadido:
 # eval de escenario ficticio después de navegar y cierre nativo desde stdin.
 generated=ROOT/'scratch/desktop-integration/generated'
 shutil.copytree(ROOT/'desktop/src-tauri',generated,ignore=shutil.ignore_patterns('target','gen','*.log'),dirs_exist_ok=True)
 cargo=generated/'Cargo.toml';cargo.write_text(cargo.read_text().replace('name = "plaudvault-desktop"','name = "plaudvault-desktop-integration"'))
 source=generated/'src/main.rs';text=source.read_text()
 old='''if navigate_window.navigate(url).is_err() {
                        handle.exit(1);
                        return;
                    }'''
 new='''if navigate_window.navigate(url).is_err() { handle.exit(1); return; }
                    std::thread::sleep(std::time::Duration::from_secs(2));
                    let script=std::fs::read_to_string(std::env::var("PV_TEST_SCRIPT").unwrap()).unwrap();
                    navigate_window.eval(&script).unwrap();
                    let mut close=String::new();
                    std::io::stdin().lock().read_line(&mut close).unwrap();
                    if close.trim()=="close" || close.is_empty() { navigate_window.close().unwrap(); }'''
 assert old in text;source.write_text(text.replace(old,new))
 target=ROOT/'scratch/tauri-probe/target'
 env=os.environ.copy();env['CARGO_TARGET_DIR']=str(target)
 subprocess.run(['cargo','build','--offline','--manifest-path',str(cargo)],env=env,check=True,stdout=subprocess.DEVNULL)
 js=work/'scenario.js';js.write_text(script)
 binary=target/'debug/plaudvault-desktop-integration'
 for mode in ['normal','forced','parent-lost']:
  reports=[];events=[];profile=work/mode;profile.mkdir()
  listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
  app=create_app(cfg,'synthetic-login',{f'127.0.0.1:{port}'},frontend_dir=ROOT/'desktop/dist')
  apps.append(app)
  @app.post('/__probe')
  async def report(request:Request):reports.append(await request.json());return {'ok':True}
  @app.middleware('http')
  async def record(request,call_next):
   response=await call_next(request)
   if request.url.path.startswith('/api/audio/'):
    events.append({'status':response.status_code,'range':request.headers.get('range')})
   return response
  server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
  thread=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);thread.start()
  servers.append((server,thread,listener))
  native_env=runtime_environment(profile);native_env['PV_TEST_SCRIPT']=str(js)
  trace=profile/'trace.log'
  with (profile/'native.log').open('wb') as log:
   proc=subprocess.Popen(['strace','-f','-qq','-e','trace=%file','-o',str(trace),str(binary)],stdin=subprocess.PIPE,stdout=log,stderr=log,env=native_env,start_new_session=True)
   tracked.append(proc)
   problem=webkit_sandbox_problem(proc.pid)
   if problem:raise RuntimeError(problem)
   proc.stdin.write(f'http://127.0.0.1:{port}/login?t=synthetic-login\n'.encode());proc.stdin.flush()
   deadline=time.monotonic()+40
   while not reports and time.monotonic()<deadline and proc.poll() is None:time.sleep(.1)
   if not reports or not reports[0].get('ok'):
    print('NATIVE FAILURE',reports,(profile/'native.log').read_text()[-3000:]);raise RuntimeError('native scenario failed')
   # Solo procesos del ensayo: inspeccionar descriptores abiertos, incluyendo borrados.
   pids={proc.pid}
   for _ in range(8):
    for p in Path('/proc').glob('[0-9]*/status'):
     try:
      status=p.read_text();parent=int(next(l.split()[1] for l in status.splitlines() if l.startswith('PPid:')))
      if parent in pids:pids.add(int(p.parent.name))
     except (OSError,StopIteration,ValueError):pass
   media=[]
   for pid in pids:
    for fd in Path(f'/proc/{pid}/fd').glob('*'):
     try:
      name=os.readlink(fd)
      if 'WebKit-Media' in name:
       mounts=Path(f'/proc/{pid}/mountinfo').read_text()
       media.append({'pid':pid,'name':name,'chunk':chunk in fd.read_bytes(),'mounts':[l for l in mounts.splitlines() if ' /tmp ' in l or ' /var/tmp ' in l or ' / ' in l]})
     except OSError:pass
   if mode=='normal':
    proc.stdin.write(b'close\n');proc.stdin.flush();proc.wait(timeout=10)
   elif mode=='parent-lost':
    proc.stdin.close();proc.wait(timeout=10)
   else:
    os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=10)
   time.sleep(.5)
   alive=[]
   for pid in pids-{proc.pid}:
    try:
     status=Path(f'/proc/{pid}/status').read_text()
     if '\nState:\tZ' not in status:alive.append(pid)
    except OSError:pass
   hits=[]
   for p in profile.rglob('*'):
    if p.is_file() and p.name not in ['trace.log','native.log']:
     if chunk in p.read_bytes():hits.append(str(p.relative_to(profile)))
   assert not alive, f'Procesos vivos después de cerrar: {alive}'
   assert not hits, f'Audio en cachés: {hits}'
   assert any(m['chunk'] and any(' /tmp ' in line and ' - tmpfs ' in line for line in m['mounts']) for m in media), 'No se verificó temporal multimedia en RAM'
   assert any(e['status']==206 for e in events), 'No se sirvieron rangos'
   assert reports[0]['queryEmpty'], 'Credencial en la URL final'
   writes=[]
   for line in trace.read_text(errors='replace').splitlines():
    if any(flag in line for flag in ['O_WRONLY','O_RDWR','O_CREAT','mkdir(','mkdirat(','rename(','unlink(']):
     paths=re.findall(r'"([^"\n]+)"',line)
     if paths:writes.append(paths[0].replace(str(work),'$RAM'))
   results.append({'write_paths':sorted(set(writes)),'mode':mode,'mp3_bytes':len(payload),'scenario':reports[0],'audio_requests':events,'media_fds':media,'alive_after_close':alive,'audio_chunks_in_profile':hits,'runtime_log':(profile/'native.log').read_text()[-500:]})
   kill_group(proc)
  app.state.vault.lock_now();server.should_exit=True;thread.join(timeout=5);listener.close()
 output=ROOT/'scratch/desktop-integration/results';output.mkdir(exist_ok=True)
 (output/'native.json').write_text(json.dumps(results,indent=2))
 print(json.dumps(results,indent=2))
finally:
 for proc in tracked:
  if proc.poll() is None:kill_group(proc)
 for app in apps:app.state.vault.lock_now()
 for server,thread,listener in servers:
  server.should_exit=True;thread.join(timeout=5);listener.close()
 # Runtime y audio sintético viven exclusivamente en RAM; no retenerlos.
 shutil.rmtree(work,ignore_errors=True)
