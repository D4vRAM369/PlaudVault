# PlaudVault

Bóveda local cifrada para grabaciones de voz (Plaud Note o cualquier fuente).
Privacidad por diseño: tus grabaciones viven en tu disco, no en la nube.

**Flujo:** dejas un audio o transcript en `inbox/` → se transcribe
(faster-whisper, CPU) o se ingiere directo si ya es texto → la transcripción
queda indexada y buscable (SQLite + FTS5) → el audio original se cifra
(AES-256-GCM, clave derivada con Argon2id) y el fichero en claro se borra →
aviso opcional por Telegram.

## Instalación

```bash
git clone <repo> && cd plaudvault
python -m venv .venv && source .venv/bin/activate
pip install -e .                      # instala el comando `plaudvault`
pip install -e '.[gui]'               # opcional: interfaz web local
cp config.example.yaml config.yaml   # ajusta rutas si quieres
```

## Uso

```bash
python -m plaudvault init                      # crea vault + passphrase
python -m plaudvault run                       # watcher sobre inbox/
python -m plaudvault ingest ~/audio/impro.mp3  # fichero suelto
python -m plaudvault search "soberanía"        # full-text (ignora tildes)
python -m plaudvault list
python -m plaudvault decrypt 7 -o ~/recuperados
python -m plaudvault sync --limit 3            # importa desde la nube de Plaud
python -m plaudvault.gui                       # interfaz web local (solo lectura)
```

## Interfaz gráfica (solo lectura)

```bash
python -m plaudvault.gui            # abre el navegador en 127.0.0.1:<puerto aleatorio>
```

- Navega por `archive/` (también tras `reclassify`): transcript, outline y
  notas con sus infografías. Ignora las carpetas `*.partial` de un sync en curso
  y se refresca sola cada 10 s.
- Búsqueda en todo (FTS5) con los fragmentos resaltados.
- Desbloqueas con tu passphrase y el audio se **descifra en memoria** y se sirve
  al reproductor; nunca se escribe un `.mp3` en claro.
- Abre la base de datos en **solo lectura**: puede estar abierta mientras corre
  `sync`.

Seguridad del servidor local: solo escucha en `127.0.0.1`, la URL de arranque
lleva un token de un solo uso que se cambia por una cookie de sesión, rechaza
cabeceras `Host` ajenas (DNS rebinding), CSP estricta (las notas se renderizan
sin HTML ni imágenes remotas) y bloqueo automático a los 15 min sin uso.

Para probarla sin tocar tu vault: `python tests/make_gui_fixture.py /tmp/pv-gui`
y después `python -m plaudvault.gui -c /tmp/pv-gui/config.yaml` (passphrase `test`).

## Importar desde la nube de Plaud

`sync` descarga todo tu histórico a carpetas agrupadas por mes:

```
archive/10-26/2026-10-14_2049_Reunión/
├── audio.pv           # audio cifrado
├── transcript.md      # con timestamps y speakers
├── outline.md
├── notes/01_Resumen.md, 02_….md   # una por plantilla
└── assets/*.png       # infografías, enlazadas desde las notas
```

Es idempotente (repetirlo solo baja lo nuevo) y cada carpeta se escribe de forma
atómica. `--transcribe-missing` transcribe con whisper las grabaciones que Plaud
no transcribió.

Necesita tu token de sesión de web.plaud.ai en la variable `PLAUD_TOKEN`
(DevTools → Network → cabecera `authorization` de cualquier petición a la API):

```bash
read -rs PLAUD_TOKEN && export PLAUD_TOKEN   # no queda en el historial
```

### Reordenar por carpetas de Plaud

Las carpetas que tienes en Plaud se guardan como categoría de cada grabación.
Tras un `sync`, `reclassify` reorganiza el archivo:

```bash
plaudvault reclassify --dry-run   # muestra qué se movería
plaudvault reclassify             # archive/<Carpeta>/MM-YY/<grabación>/
plaudvault list --categories      # carpetas y nº de grabaciones
plaudvault list --category Ideas
```

Localiza cada grabación por su marca `.plaud_id` (no por la ruta), actualiza la
ruta del audio en la base de datos y se puede repetir sin riesgo. No se ejecuta
si detecta un `sync` en curso.

> ⚠️ Usa la API web **no oficial** de Plaud: puede cambiar sin aviso. Todo el
> acceso está aislado en `plaudvault/plaud_client.py`.

Telegram (opcional): pon `telegram.enabled: true` en config.yaml y exporta
`TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` en el entorno.

## Modelo de seguridad

- La passphrase **nunca** se guarda. Se deriva una clave AES-256 con
  Argon2id (64 MiB, t=3) en cada sesión y se verifica contra
  `vault.keycheck`.
- Se cifra el **audio**; transcripciones, notas e infografías quedan en claro para que FTS5
  pueda indexarlas. Protege contra robo del disco, no contra malware
  activo en tu sesión.
- Sin passphrase no hay recuperación posible. No hay puerta trasera.

### Copias de seguridad

`vault.db` está en modo **WAL**: junto a ella verás `vault.db-wal` y
`vault.db-shm`. Forman parte de la base de datos, **no los borres**. Para
copiarla sin cerrar nada, usa la API de backup de SQLite:

```bash
python -c "import sqlite3; sqlite3.connect('vault.db').backup(sqlite3.connect('copia.db'))"
```

## Estructura

```
plaudvault/
├── __main__.py      # CLI (init, run, ingest, search, show, list, decrypt, sync, reclassify)
├── config.py        # carga de config.yaml
├── db.py            # SQLite + FTS5 + triggers
├── crypto.py        # Argon2id + AES-256-GCM
├── transcriber.py   # faster-whisper (audio) / passthrough (texto)
├── pipeline.py      # orquestación de la ingesta
├── plaud_client.py  # API web de Plaud (región, paginación, S3)
├── sync.py          # importación de la nube a carpetas por mes
├── reclassify.py    # reordena archive/ por carpetas de Plaud
├── gui.py           # interfaz web local de solo lectura (FastAPI)
├── gui_static/      # HTML + JS + CSS sin build
├── notifier.py      # Telegram (HTTP directo)
└── watcher.py       # watchdog sobre inbox/
tests/               # python tests/test_*.py (sin red ni credenciales)
templates/           # plantillas CoSTAR (fase 1.5, aún no activas)
```

## Roadmap

- **MVP (esto):** ingesta, transcripción, cifrado, búsqueda, Telegram saliente.
- **1.5:** enriquecimiento LLM con plantillas CoSTAR (Anthropic/Ollama),
  export a Obsidian.
- **2:** UI de escritorio. 🔄 Hecho el primer paso: GUI web local de solo lectura
  (`plaudvault.gui`), cuya API será el backend de Tauri v2 + Svelte 5. Bot de
  Telegram bidireccional.
- **3:** ~~syncer con la API no oficial de Plaud~~ ✅ hecho (`sync`).

Licencia: [GPL-3.0](LICENSE)
