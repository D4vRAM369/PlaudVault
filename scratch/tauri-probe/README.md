# Spike Tauri / WebKitGTK (datos ficticios)

Prueba aislada, no interfaz de producción. Requiere Rust, GTK3/WebKitGTK 4.1,
strace, display X11 y dependencias Cargo ya disponibles. No instala paquetes.

```sh
cargo build --offline --manifest-path scratch/tauri-probe/Cargo.toml
python scratch/tauri-probe/run_probe.py
PV_PROBE_SECONDS=450 python scratch/tauri-probe/run_probe.py
PV_PROBE_NO_PROXY=1 python scratch/tauri-probe/run_probe.py
```

Genera un WAV sinusoidal solamente en memoria. HOME/XDG/TMPDIR temporales
viven en `/dev/shm`. Fuerza sandbox WebKit, usa contexto efímero y proxy
cerrado con excepción para 127.0.0.1. La dirección 127.0.0.2:18762 es un
servidor sintético para comparar con/sin proxy; no necesita internet.

Captura `/proc` de descendientes, descriptores multimedia borrados y su
mountinfo, peticiones del reproductor y strace de rutas/red. El detector de
fragmentos tiene un control positivo: escribe/busca/elimina un señuelo
ficticio en el directorio de este spike. Caches y trazas quedan en RAM para
inspección; el launcher termina el grupo de procesos con SIGTERM.

Las ejecuciones dentro del sandbox de Codex pueden bloquear sockets; los
resultados medidos se obtuvieron fuera de esa restricción. No debe copiarse
esta prueba a producción como una garantía general de «cero rastro».
