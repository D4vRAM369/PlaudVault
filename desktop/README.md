# PlaudVault · Bóveda nocturna

Frontend Svelte 5 + TypeScript para el lanzador Tauri. Conserva las tres columnas del mockup elegido: carpetas y origen, biblioteca y búsqueda, documentos y audio. No añade escritura, importación ni sincronización gráfica.

## Desarrollo

Requiere Node.js 20.19+ o 22.12+ y npm. Desde `desktop/`:

```bash
npm ci
npm run check
npm run build
```

`dist/` es el resultado servido por el backend Python: `/` entrega `dist/index.html`, y `/desktop/assets/` los recursos compilados. Las llamadas a `/api/` utilizan la misma sesión y origen que el backend existente. Para inspeccionar únicamente el diseño con Vite, `npm run dev`; la API autenticada necesita el lanzador/backend, no se incluyen datos ficticios ni un proxy de autenticación.

## Privacidad y alcance

No usa almacenamiento del navegador, recursos externos, telemetría ni descarga de audio en Blob. El reproductor nativo recibe el audio desde la API mediante su URL local y peticiones Range. Bloquear retira la fuente del reproductor; la aplicación comprueba también el estado del bloqueo cada diez segundos. Las transcripciones siguen visibles porque están en claro.

Estas propiedades del frontend no demuestran «cero rastro». La comprobación de sandbox, RAM, red, rastros y cierre pertenece al lanzador Tauri y sus pruebas de integración. No se muestra esa garantía en esta interfaz.

## Compilar y abrir Tauri en Linux

Además del entorno Python con `.[gui]`, necesitas Rust/Cargo y las bibliotecas de desarrollo de WebKitGTK 4.1, GTK 3, libayatana-appindicator, librsvg, libxdo y OpenSSL. En Debian/Ubuntu los paquetes habituales son `libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev libxdo-dev libssl-dev` y las herramientas de compilación. Las dependencias nativas dependen de la distribución.

Desde la raíz del repositorio, después de compilar Svelte:

```bash
cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml
.venv/bin/python -m plaudvault.desktop
```

Si usas otro directorio de compilación, pasa `--binary /ruta/al/binario/plaudvault-desktop`. Para otra configuración del vault usa `-c`; para otro frontend compilado, `--frontend`.

El lanzador conserva el backend de solo lectura en `127.0.0.1`, verifica tmpfs y el sandbox del renderer en `/proc`, y entrega el enlace de sesión al binario por stdin. El token no se pasa como argumento ni se imprime. El proceso web recibe HOME, XDG y temporales en RAM; no hereda tokens del entorno. La ventana comienza con un bootstrap vacío y solo recibe la sesión del vault después de verificar el renderer. Al cerrar se termina el servidor y se elimina el perfil efímero.

La prerelease v0.3.0-rc.1 incluye instalador `.deb` y portable con Python para Ubuntu 24.04 x86_64. Esta sección describe la compilación desde fuentes; para instalar sin compilar, consulta [packaging/README.md](../packaging/README.md).

## Verificación

```bash
npm run check
npm run build
cargo test --locked --manifest-path src-tauri/Cargo.toml
```

El ensayo nativo reproducible está en `../scratch/desktop-integration/run.py` y usa únicamente datos ficticios. Requiere `ffmpeg`, `strace`, Rust, las dependencias nativas, una sesión gráfica y `/dev/shm` escribible. No sustituye una auditoría del sistema. Véase `../assets/tauri-security.md` para condiciones y límites.
