"""Synthetic-only launcher: all mutable runtime directories are under RAM."""
import os, pathlib, tempfile, subprocess, json, time, signal, io, wave, math, struct, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
requests=[]
audio=io.BytesIO()
with wave.open(audio,"wb") as f:
 f.setparams((1,2,44100,0,"NONE","not compressed"))
 f.writeframes(b"".join(struct.pack("<h",int(12000*math.sin(2*math.pi*440*i/44100))) for i in range(44100))*int(os.environ.get("PV_PROBE_SECONDS","30")))
payload=audio.getvalue()
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  requests.append({"path":self.path,"range":self.headers.get("Range")})
  start=0;end=len(payload)-1
  if self.headers.get("Range"):
   part=self.headers["Range"].split("=")[1].split("-");start=int(part[0] or 0);end=int(part[1]) if part[1] else end
  body=payload[start:end+1] if self.path=="/audio.wav" else b"ok"
  self.send_response(206 if self.headers.get("Range") else 200);self.send_header("Content-Type","audio/wav" if self.path=="/audio.wav" else "text/plain");self.send_header("Accept-Ranges","bytes");self.send_header("Content-Range",f"bytes {start}-{end}/{len(payload)}");self.send_header("Cache-Control","no-store");self.send_header("Access-Control-Allow-Origin","*");self.send_header("Content-Length",str(len(body)));self.end_headers()
  try: self.wfile.write(body)
  except (BrokenPipeError,ConnectionResetError): pass
 def log_message(self,*args): pass
server=ThreadingHTTPServer(("127.0.0.1",18761),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
canary_server=ThreadingHTTPServer(("127.0.0.2",18762),Handler)
threading.Thread(target=canary_server.serve_forever,daemon=True).start()
root = pathlib.Path(tempfile.mkdtemp(prefix='pv-tauri-probe-',dir='/dev/shm'))
for name in ['home','cache','data','config','tmp']: (root/name).mkdir()
env = os.environ.copy()
for key,name in [('HOME','home'),('XDG_CACHE_HOME','cache'),('XDG_DATA_HOME','data'),('XDG_CONFIG_HOME','config'),('TMPDIR','tmp')]: env[key]=str(root/name)
# No security bypass variables are accepted from the caller.
for key in list(env):
 if key.startswith('WEBKIT_') or key in ['GDK_BACKEND']: env.pop(key)
env['WEBKIT_FORCE_SANDBOX']='1'
exe=pathlib.Path(__file__).parent/'target/debug/plaudvault-tauri-probe'
command=[str(exe.resolve())]
with (root/'runtime.log').open('wb') as log:
 proc=subprocess.Popen(['strace','-f','-qq','-e','trace=%file,%network','-o',str(root/'trace.log')]+command,env=env,stdout=log,stderr=log,start_new_session=True)
 time.sleep(12)
 rows=[]
 ancestors={proc.pid}
 for _ in range(5):
  for item in pathlib.Path('/proc').glob('[0-9]*/status'):
   try:
    text=item.read_text();ppid=int(text.split('PPid:')[1].splitlines()[0]);pid=int(item.parent.name)
    if ppid in ancestors: ancestors.add(pid)
   except (OSError,ValueError,IndexError): pass
 for path in pathlib.Path('/proc').glob('[0-9]*/status'):
  try:
   status=path.read_text(); cmd=(path.parent/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
   if int(path.parent.name) in ancestors:
    rows.append({'pid':int(path.parent.name),'cmd':cmd,'status':[line for line in status.splitlines() if line.startswith(('Name:','PPid:','NoNewPrivs:','Seccomp:','Seccomp_filters:'))]})
  except OSError: pass
 media_fds=[]
 for pid in ancestors:
  for fd in pathlib.Path(f'/proc/{pid}/fd').glob('*'):
   try:
    link=os.readlink(fd)
    if 'WebKit-Media' in link:
     data=fd.read_bytes();info=pathlib.Path(f'/proc/{pid}/mountinfo').read_text()
     media_fds.append({'pid':pid,'fd':fd.name,'link':link,'size':len(data),'synthetic_chunk_present':payload[100000:104096] in data,'mounts':[l for l in info.splitlines() if ' /var' in l or ' /tmp' in l or ' / ' in l]})
   except OSError: pass
 print(json.dumps({'media_fds':media_fds,'ram_root':str(root),'returncode':proc.poll(),'requests':requests,'processes':rows,'files':[{'path':str(p.relative_to(root)),'size':p.stat().st_size} for p in root.rglob('*') if p.is_file()],'log':(root/'runtime.log').read_text(errors='replace')},indent=2))
 if proc.poll() is None:
  os.killpg(proc.pid,signal.SIGTERM);proc.wait(timeout=10)

server.shutdown()
canary_server.shutdown()
chunk=payload[100000:104096]
canary=pathlib.Path(__file__).parent/'detector-canary.bin'
canary.write_bytes(chunk)
positive=chunk in canary.read_bytes()
canary.unlink()
hits=[]
for p in root.rglob('*'):
 if p.is_file() and p.name not in ['trace.log','runtime.log']:
  try:
   if chunk in p.read_bytes(): hits.append(str(p.relative_to(root)))
  except OSError: pass
print(json.dumps({'detector_positive_control':positive,'ram_audio_chunk_hits':hits,'scope':'RAM runtime directories only; trace must establish persistent write scope'}))
