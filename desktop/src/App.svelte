<script lang="ts">
  import { onMount } from 'svelte';
  type Recording = {key:string;group:string;title:string;indexed:boolean;source?:string;duration_secs?:number;ingested_at?:string};
  type Hit = {key:string;filename:string;kind:string;frag:string};
  let recordings = $state<Recording[]>([]), hits = $state<Hit[]>([]);
  let selected = $state<Recording | null>(null), docs = $state<string[]>([]), activeDoc = $state(''), html = $state('');
  let cryptoEnabled = $state(true);
  let locked = $state(true), hasAudio = $state(false), audioReady = $state(false), loading = $state(true), reading = $state(false);
  let error = $state(''), docError = $state(''), audioError = $state(''), query = $state(''), searchError = $state('');
  let filter = $state('all'), unlockOpen = $state(false), passphrase = $state(''), unlockError = $state(''), unlocking = $state(false);
  let audio = $state<HTMLAudioElement | undefined>();
  let secretInput = $state<HTMLInputElement | undefined>();
  let selectionVersion = 0, documentVersion = 0, searchVersion = 0, lockVersion = 0;
  let searchTimer: ReturnType<typeof setTimeout>;
  const groups = $derived([...new Set(recordings.map(r => r.group))].sort((a,b) => a.localeCompare(b,'es')));
  const visible = $derived(recordings.filter(r => filter === 'all' || (filter === 'local' ? r.source === 'local' : filter === 'plaud' ? r.source !== 'local' : r.group === filter.slice(6))));
  const keyPath = (key:string) => encodeURIComponent(key);
  const label = (name:string) => name.replace(/^notes\//,'').replace(/\.md$/i,'').replace(/_/g,' ');
  const sourceLabel = (r:Recording) => r.source === 'local' ? 'Archivo local' : 'Desde Plaud';
  function duration(seconds?:number) { if (seconds == null) return ''; return `${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,'0')}`; }
  function date(value?:string) { if (!value) return ''; const d = new Date(value); return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('es', {day:'numeric',month:'short',year:'numeric'}); }
  async function request<T>(path:string, options:RequestInit = {}):Promise<T> {
    const response = await fetch(path,{...options,cache:'no-store',credentials:'same-origin',redirect:'error'});
    if (!response.ok) { if (response.status === 401) throw new Error('La sesión ha terminado. Cierra y vuelve a abrir la aplicación.'); if (response.status === 403) throw new Error('Acceso rechazado. Comprueba que la sesión sigue abierta.'); throw new Error(`No se pudo completar la operación (${response.status}).`); }
    return response.json();
  }
  function stopAudio() { audioReady = false; if (audio) { audio.pause(); audio.removeAttribute('src'); audio.load(); } }
  function applyLock(value:boolean) { locked = value; if (value) stopAudio(); }
  async function refresh() {
    const version = lockVersion;
    try { const data = await request<{recordings:Recording[];locked:boolean;crypto_enabled?:boolean}>('/api/recordings'); recordings = data.recordings; cryptoEnabled=data.crypto_enabled ?? true; if (version === lockVersion) applyLock(data.locked); error = ''; if (selected && !recordings.some(r => r.key === selected!.key)) { ++selectionVersion; ++documentVersion; stopAudio(); selected=null; docs=[];html=''; } }
    catch (e) { error = (e as Error).message; stopAudio(); }
    finally { loading = false; }
  }
  async function openRecording(r:Recording, preferred?:string) {
    const version = ++selectionVersion; ++documentVersion; stopAudio(); selected=r; docs=[];html='';activeDoc='';docError='';audioError='';hasAudio=false;reading=true;
    try { const data = await request<{docs:string[];has_audio:boolean}>(`/api/rec/${keyPath(r.key)}`); if(version !== selectionVersion) return; docs=data.docs;hasAudio=data.has_audio; if (docs.length) await openDoc(preferred && docs.includes(preferred) ? preferred : docs[0]); }
    catch(e) { if(version === selectionVersion) docError=(e as Error).message; }
    finally { if(version === selectionVersion) reading=false; }
  }
  async function openDoc(name:string) {
    if(!selected) return; const version=++documentVersion, key=selected.key; activeDoc=name;html='';docError='';reading=true;
    try { const data=await request<{html:string}>(`/api/doc/${keyPath(key)}?${new URLSearchParams({name})}`); if(version === documentVersion) html=data.html; }
    catch(e) { if(version === documentVersion) docError=(e as Error).message; }
    finally { if(version === documentVersion) reading=false; }
  }
  function queueSearch() { clearTimeout(searchTimer); const version=++searchVersion; hits=[];searchError=''; if(!query.trim()) return; const q=query; searchTimer=setTimeout(async()=>{ try { const data=await request<{results:Hit[]}>(`/api/search?${new URLSearchParams({q})}`); if(version===searchVersion) hits=data.results; } catch(e) { if(version===searchVersion) searchError=(e as Error).message; } },250); }
  async function lock() { ++lockVersion; applyLock(true); try { await request('/api/lock',{method:'POST'}); } catch(e) { error=(e as Error).message; } }
  async function unlock() { if(unlocking || !passphrase) return; unlocking=true;unlockError=''; const secret=passphrase;passphrase=''; ++lockVersion;
    try { await request('/api/unlock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({passphrase:secret})}); locked=false;unlockOpen=false; }
    catch { unlockError='No se pudo desbloquear el audio. Comprueba tu passphrase y la conexión local.'; secretInput?.focus(); }
    finally { unlocking=false; }
  }
  function showUnlock() { unlockOpen=true;unlockError='';passphrase=''; setTimeout(()=>secretInput?.focus(),0); }
  function startAudio() { audioError='';audioReady=true; }
  onMount(()=>{ void refresh(); const interval=setInterval(()=>void refresh(),10000); return ()=>{clearInterval(interval);clearTimeout(searchTimer);++selectionVersion;++documentVersion;++searchVersion;stopAudio();}; });
</script>

<svelte:head><title>PlaudVault · Tu biblioteca</title></svelte:head>
<header class="top"><div class="brand"><span aria-hidden="true">▣</span> PlaudVault</div><span class="top-caption">TU ARCHIVO, EN TU DISCO</span><span class="local-pill"><i></i> Biblioteca local</span></header>
<div class="shell">
  <aside class="sidebar" aria-label="Filtros de biblioteca">
    <div class="eyebrow">Tu espacio</div>
    {#each [['all','Todas las grabaciones'],['local','Archivos locales'],['plaud','Desde Plaud']] as [id,title]}
      <button class:chosen={filter===id} class="nav" onclick={()=>filter=id}><span>{title}</span><small>{recordings.filter(r=>id==='all'||(id==='local'?r.source==='local':r.source!=='local')).length}</small></button>
    {/each}
    <div class="eyebrow folder-heading">Carpetas</div>
    {#each groups as group}<button class="nav" class:chosen={filter===`group:${group}`} onclick={()=>filter=`group:${group}`}><span>{group || 'Sin carpeta'}</span><small>{recordings.filter(r=>r.group===group).length}</small></button>{/each}
    <div class="vault-status"><i></i> Solo lectura<p>{cryptoEnabled?'Audio cifrado.':'Audio sin cifrar.'}<br>Textos y notas en claro.</p></div>
  </aside>
  <section class="listing" aria-label="Biblioteca">
    <div class="eyebrow">VOCES · IDEAS · CONVERSACIONES</div><h2>Tu biblioteca<span>.</span></h2>
    <label class="search"><span aria-hidden="true">⌕</span><input type="search" bind:value={query} oninput={queueSearch} placeholder="Buscar en todo el archivo" aria-label="Buscar en transcripciones y notas" autocomplete="off" spellcheck="false"></label>
    <div class="list-caption">{query.trim()?'Resultados de búsqueda':`${visible.length} grabaciones`}</div>
    {#if loading}<p class="empty" role="status">Abriendo tu biblioteca…</p>{:else if error}<div class="error" role="alert">{error}<button class="secondary" onclick={refresh}>Volver a intentar</button></div>{/if}
    <div class="records">
    {#if query.trim()}
      {#if searchError}<p class="error" role="alert">{searchError}</p>{:else if !hits.length}<p class="empty" role="status">No hay coincidencias.</p>{/if}
      {#each hits as hit}<button class="rec" class:selected={selected?.key===hit.key} onclick={()=>{const r=recordings.find(r=>r.key===hit.key);if(r) void openRecording(r,hit.kind);}}><span class="type">{label(hit.kind)}</span><strong>{hit.filename}</strong><span class="snippet">{#each hit.frag.split(/(\x02[^\x03]*\x03)/g) as part}{#if part.startsWith('\x02')}<mark>{part.slice(1,-1)}</mark>{:else}{part}{/if}{/each}</span></button>{/each}
    {:else}
      {#if !loading && !visible.length}<p class="empty">{recordings.length?'Esta carpeta aún no tiene grabaciones.':'Tu biblioteca empieza aquí. Importa grabaciones con la CLI para verlas en este espacio.'}</p>{/if}
      {#each visible as r}<button class="rec" class:selected={selected?.key===r.key} onclick={()=>void openRecording(r)}><span class="type">{sourceLabel(r)}</span><strong>{r.title}</strong><span class="meta"><span>{r.group || 'Sin carpeta'}</span><span>{duration(r.duration_secs)}</span></span>{#if r.ingested_at}<span class="rec-date">{date(r.ingested_at)}</span>{/if}</button>{/each}
    {/if}
    </div>
  </section>
  <main class="reader">
    <div class="reader-toolbar"><span class="eyebrow">{selected?.group || 'TU BÓVEDA'}</span>{#if cryptoEnabled}<button class="secondary lock" onclick={()=>locked?showUnlock():void lock()}>{locked?'Desbloquear audio':'Bloquear audio'}</button>{/if}</div>
    {#if selected}
      <span class="pill">{sourceLabel(selected)}</span><h1>{selected.title}</h1><div class="detail-meta"><span>{date(selected.ingested_at)}</span><span>{duration(selected.duration_secs)}</span><span>Solo lectura</span></div>
      <nav class="tabs" aria-label="Documentos de la grabación">{#each docs as name}<button class:active={activeDoc===name} aria-current={activeDoc===name?'page':undefined} onclick={()=>void openDoc(name)}>{label(name)}</button>{/each}</nav>
      {#if reading}<p class="empty" role="status">Abriendo documento…</p>{:else if docError}<p class="error" role="alert">{docError}</p>{:else if !docs.length}<p class="empty">Esta grabación no tiene documentos disponibles.</p>{:else}<article class="document">{@html html}</article>{/if}
      {#if hasAudio}<section class="player" aria-label="Reproductor de audio"><div class="player-heading"><span class="eyebrow">AUDIO ORIGINAL</span><span class="pill">{cryptoEnabled?(locked?'Bloqueado':'Desbloqueado'):'Sin cifrar'}</span></div>{#if locked}<p>Tu audio está protegido por la passphrase.</p><button class="primary" onclick={showUnlock}>Desbloquear para escuchar</button>{:else if !audioReady}<button class="primary" onclick={startAudio}>Preparar reproducción</button>{:else}<audio bind:this={audio} controls preload="none" src={`/api/audio/${keyPath(selected.key)}`} onerror={()=>{audioError='No se pudo reproducir el audio. Comprueba que sigue desbloqueado y que el formato es compatible.';void refresh();}}></audio>{/if}{#if audioError}<p class="error" role="alert">{audioError}</p>{/if}</section>{/if}
    {:else}<div class="welcome"><div class="vault-symbol" aria-hidden="true">▣</div><div class="eyebrow">UN LUGAR PARA VOLVER A ESCUCHAR</div><h1>Tus voces.<br>Tu archivo.</h1><p>Selecciona una grabación para leer sus documentos y escuchar su audio.</p><div class="welcome-note">La biblioteca es de solo lectura.<br>Las transcripciones y las notas permanecen visibles cuando bloqueas el audio.</div></div>{/if}
  </main>
</div>
<footer><span><i></i> PLAUDVAULT / BÓVEDA NOCTURNA</span><span>Local · Solo lectura · {cryptoEnabled?(locked?'Audio bloqueado':'Audio desbloqueado'):'Audio sin cifrar'}</span></footer>
{#if unlockOpen}<div class="modal-backdrop"><div class="unlock-panel" role="dialog" aria-modal="true" aria-labelledby="unlock-title" tabindex="-1" onkeydown={(event)=>{if(event.key==='Escape'&&!unlocking){unlockOpen=false;passphrase='';} if(event.key==='Tab'){const targets=Array.from(event.currentTarget.querySelectorAll<HTMLElement>('input,button')).filter(el=>!el.hasAttribute('disabled'));const first=targets[0],last=targets.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}}}><div class="eyebrow">SOLO EN ESTA SESIÓN</div><h2 id="unlock-title">Vuelve a escuchar.</h2><p>Introduce tu passphrase para desbloquear el audio. Los textos y las notas permanecen en claro.</p><label for="passphrase">Passphrase</label><input id="passphrase" bind:this={secretInput} class="secret" type={typeof CSS!=='undefined'&&CSS.supports('-webkit-text-security','disc')?'text':'password'} bind:value={passphrase} autocomplete="off" autocapitalize="off" spellcheck="false" onkeydown={(event)=>{if(event.key==='Enter')void unlock();}} disabled={unlocking}>{#if unlockError}<p class="error" role="alert">{unlockError}</p>{/if}<button class="primary" onclick={unlock} disabled={unlocking||!passphrase}>{unlocking?'Desbloqueando…':'Desbloquear audio'}</button><button class="secondary" disabled={unlocking} onclick={()=>{unlockOpen=false;passphrase='';}}>Cancelar</button></div></div>{/if}
