# Seguridad del escritorio Tauri: condiciones y pruebas

Estado: experimental. Pruebas realizadas el 15 de octubre de 2026 en Linux,
con Tauri 2.9.5, WebKitGTK 2.52.6 y GTK 3.24.41.

## Resultado y alcance

El ensayo no encontró fragmentos del audio sintético fuera de RAM en los
archivos y temporales inspeccionados. Se verificaron reproducción real,
saltos, renderer con sandbox y cierre de procesos. Esto respalda la viabilidad
de una sesión sin rastros de audio en disco **bajo las condiciones medidas**;
no es una garantía universal ni una auditoría de todas las distribuciones.

La GUI Chromium existente conserva su procedimiento y sus requisitos. El
resultado de Chromium no se ha usado como prueba de WebKitGTK.

## Arranque protegido

- Solo Linux y directorios tmpfs/ramfs verificados. El lanzador rechaza un
  `XDG_RUNTIME_DIR` que no esté respaldado por RAM.
- HOME, configuración, datos, temporales y cachés del proceso web en un perfil
  efímero en RAM, incluida la caché de Mesa y el registro de GStreamer.
- WebKit recibe `WEBKIT_FORCE_SANDBOX=1`. El contexto confirma sandbox y modo
  efímero; el padre comprueba el **renderer real**, con `NoNewPrivs: 1` y
  `Seccomp: 2`, antes de entregar la sesión autenticada por stdin.
- El frontend no recibe permisos de shell ni filesystem de Tauri. Las
  navegaciones se limitan al origen local elegido por el lanzador.
- Proxy cerrado para las peticiones de WebKit, con excepción de localhost.
  La API conserva autenticación por cookie, Host permitido, CSP y `no-store`.
- Audio mediante el reproductor nativo y respuestas HTTP/Range, sin Blob,
  URLs de datos ni descargas a archivo.
- El binario sale si desaparece el canal del padre. El lanzador termina el
  backend y el grupo de procesos al cerrar y elimina el perfil efímero.

## Pruebas realizadas

### Motor y red

`scratch/tauri-probe/` contiene un ensayo aislado con WAV sintético de
39.690.044 bytes. Se comprobó tiempo de reproducción creciente y se inspeccionó
el descriptor multimedia mientras estaba abierto y borrado.

El aislamiento de red se comparó usando el mismo servidor de control local
en una dirección sin excepción de proxy: fue accesible sin bloqueo y dejó de
recibir peticiones con el proxy cerrado, mientras el audio en localhost seguía
reproduciéndose. No se concluyó bloqueo de red a partir de un destino que ya
fuera inaccesible sin proxy.

### Aplicación integrada

Desde la raíz del repositorio, después de compilar el frontend y el binario:

```bash
.venv/bin/python scratch/desktop-integration/run.py
```

Requiere sesión gráfica, WebKitGTK, `ffmpeg`, `strace` y las dependencias de
compilación disponibles en la caché de Cargo. Usa un vault ficticio y genera
el audio en `/dev/shm`; no abre la configuración ni las grabaciones del usuario.
Genera un binario de prueba a partir de las fuentes nativas reales, con un
escenario automatizado añadido para interactuar y cerrar la ventana.

Se comprobó:

1. Carga de tres grabaciones ficticias y apertura de sus documentos con el
   frontend Svelte compilado y la API real.
2. Desbloqueo desde la interfaz con una passphrase de prueba.
3. Reproducción de MP3 sintético de **38.401.089 bytes** y salto a aproximadamente
   el segundo 528 de una grabación de 960 segundos.
4. Respuestas multimedia y peticiones Range efectivas; no basta con abrir una
   ventana o descargar bytes sin reproducirlos.
5. Inspección de `/proc/<pid>/fd` y de los montajes del proceso que conserva el
   audio. El fragmento sintético estaba en un temporal multimedia abierto,
   pero su filesystem era tmpfs.
6. Registro de rutas abiertas para escritura. Los datos de perfil y cachés
   estaban bajo RAM; las rutas de sesión de `/run/user` también estaban en
   tmpfs en el equipo medido. Los accesos a dispositivos y a los mapas de
   namespaces se distinguieron de escrituras en almacenamiento persistente.
7. Cierre normal de ventana, terminación forzada y pérdida del canal del
   padre: sin procesos del ensayo vivos tras el cierre y sin fragmentos de
   audio en los archivos de perfil inspeccionados.

El ensayo aislado incluye un control positivo del detector: un archivo
señuelo con un fragmento del audio sí fue detectado. Los resultados detallados
del ensayo integrado se guardan localmente en
`scratch/desktop-integration/results/native.json` (ignorado por Git).

### La ruta aparente de un temporal no basta

La traza mostraba `/var/tmp/WebKit-Media-*`. Inspeccionar el descriptor real y
`mountinfo` del renderer demostró que correspondía a `/tmp` sobre tmpfs dentro
del namespace de WebKit. No se clasificó como disco por su nombre ni se ignoró
por estar borrado: se leyeron sus bytes y se verificó el montaje efectivo.

## Límites

- Transcripciones, notas y sus imágenes continúan en claro: no se cifra todo
  el vault. El audio cifrado protege frente al robo del disco, no frente a
  malware de la sesión.
- Sin swap cifrado, la RAM puede acabar en almacenamiento persistente.
- El sandbox comprobado es el del renderer de WebKit. El proceso nativo y
  otros procesos auxiliares no tienen necesariamente la misma política
  seccomp; no se describe como aislamiento total de todo el programa.
- No se han auditado todos los servicios externos de la sesión gráfica,
  registros del sistema, volcados de memoria ni todas las versiones de
  WebKitGTK, GStreamer y controladores gráficos.
- No se ha reproducido un archivo completo de 16 minutos ni todos los
  formatos. Se verificaron reproducción efectiva y salto dentro de ese MP3.
- Matar abruptamente el lanzador puede dejar el directorio efímero en RAM
  hasta que se limpie o reinicie la sesión; no equivale a un archivo en disco.
- Nuevas versiones del motor, cambios de lanzamiento, cachés o reproducción
  requieren repetir los ensayos. Ejecutar el binario fuera del lanzador no
  constituye el procedimiento verificado.
