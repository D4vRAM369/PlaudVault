# Ensayo integrado de escritorio

Desde la raíz: `.venv/bin/python scratch/desktop-integration/run.py`.

Requiere frontend compilado, dependencias de Cargo disponibles, WebKitGTK,
ffmpeg, strace, sesión gráfica y tmpfs. Construye un binario instrumentado desde
las fuentes reales; nunca modifica el binario de la aplicación. Genera vault,
passphrase conocida y MP3 ficticios en RAM, navega/desbloquea/reproduce/salta y
comprueba los procesos tras cierre normal, forzado y pérdida del padre.

Resultados en `results/native.json`, ignorado por Git. El escenario automatiza
el frontend mediante evaluación nativa de JavaScript solo en el binario del
ensayo. No añade un puerto de depuración ni comandos al binario entregable.
No usar con datos reales. Alcance y límites en `docs-public/tauri-security.md`.
