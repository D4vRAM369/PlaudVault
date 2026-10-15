# Instalador Linux y publicación experimental

Los paquetes incluyen el intérprete Python, la CLI, el servidor local, el frontend
y la ventana Tauri. No necesitas Python, Node ni Rust para usarlos. Esta primera
distribución está dirigida a **Ubuntu 24.04, x86_64** (glibc 2.39); no se ha
establecido compatibilidad con otras distribuciones ni arquitecturas.

Tauri se inicia desde el lanzador Python que prepara el entorno de seguridad.
El ejecutable nativo por separado no es el punto de entrada público. Por eso
`bundle.active` permanece desactivado en la configuración de Tauri.

## Instalar el paquete Debian

Descarga el `.deb` y `SHA256SUMS` de la misma prerelease de GitHub. Desde el
directorio de descarga:

```bash
sha256sum --ignore-missing --check SHA256SUMS
sudo apt install ./plaudvault_0.3.0~rc1_amd64.deb
plaudvault --help
plaudvault desktop
```

También aparece una entrada PlaudVault en el menú de aplicaciones. `apt` instala
los componentes del sistema declarados por el paquete: WebKitGTK 4.1, GTK 3,
bubblewrap y los complementos multimedia de GStreamer. Es necesario disponer de
una sesión gráfica para abrir la ventana.

Para desinstalar el programa:

```bash
sudo apt remove plaudvault
```

El archivo personal y su configuración no forman parte del paquete instalado.

## Usar la distribución portable

Descarga el `.tar.gz` y `SHA256SUMS` de la misma prerelease y compruébalos antes
de extraer. Este formato incluye Python, pero sigue necesitando las bibliotecas
del sistema; no es un AppImage ni un binario estático.

```bash
sha256sum --ignore-missing --check SHA256SUMS
sudo apt install libwebkit2gtk-4.1-0 libgtk-3-0t64 bubblewrap gstreamer1.0-plugins-good gstreamer1.0-libav
tar -xzf PlaudVault-0.3.0-rc.1-linux-x86_64.tar.gz
```

Abre una terminal en la carpeta extraída que contiene `plaudvault`:

```bash
./plaudvault --help
./plaudvault desktop
```

## Configuración y comandos

El lanzador utiliza la configuración del usuario en
`$XDG_CONFIG_HOME/plaudvault/config.yaml`, o en
`~/.config/plaudvault/config.yaml` si esa variable no está definida. Si no existe,
usa la plantilla incluida, cuyas rutas apuntan a `~/PlaudVault`. Abrir la GUI no
crea automáticamente un archivo de configuración.

Puedes copiar la plantilla y ajustar las rutas:

```bash
mkdir -p ~/.config/plaudvault
cp /usr/share/doc/plaudvault/config.example.yaml ~/.config/plaudvault/config.yaml
```

En la distribución portable, la plantilla está junto al ejecutable:

```bash
cp config.example.yaml ~/.config/plaudvault/config.yaml
```

La CLI mantiene los subcomandos del proyecto. Por ejemplo:

```bash
plaudvault init
plaudvault search "reunión"
plaudvault -c config.yaml desktop
```

Para la versión portable sustituye `plaudvault` por `./plaudvault`. La GUI sigue
siendo de solo lectura: el token y la sincronización se gestionan con la CLI.
Consulta el README principal para el procedimiento del token sin guardarlo en
disco. La transcripción local puede descargar el modelo de Whisper la primera
vez que se utiliza; el modelo no está incluido en el instalador.

## Alcance de seguridad

Solo se cifra el audio; transcripciones y notas permanecen en claro para buscar.
La distribución conserva el lanzador que verifica el aislamiento antes de abrir
la ventana. La creación del paquete y sus pruebas de CLI no demuestran por sí
solas ausencia de rastros en un equipo: revisa las condiciones y límites del
[informe de Tauri](../assets/tauri-security.md), incluido el swap. Esta versión
es experimental.

## Construir desde el repositorio

En Ubuntu 24.04 x86_64, instala Python 3.12 con soporte para entornos virtuales,
Node 22, Rust estable y las dependencias nativas:

```bash
sudo apt install build-essential pkg-config python3.12-venv libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev libxdo-dev libssl-dev libgtk-3-dev bubblewrap gstreamer1.0-plugins-good gstreamer1.0-libav ffmpeg strace xvfb
python3.12 -m venv .venv-build
.venv-build/bin/python -m pip install -e '.[gui,test]'
.venv-build/bin/python -m pip install -r packaging/requirements-build.txt
npm --prefix desktop ci
npm --prefix desktop run check
npm --prefix desktop run build
cargo fmt --manifest-path desktop/src-tauri/Cargo.toml --check
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml
cargo build --release --locked --manifest-path desktop/src-tauri/Cargo.toml
.venv-build/bin/python packaging/build_linux.py --native desktop/src-tauri/target/release/plaudvault-desktop --version 0.3.0-rc.1
.venv-build/bin/python packaging/smoke_package.py --deb dist/release/plaudvault_0.3.0~rc1_amd64.deb
```

El constructor usa PyInstaller fijado en `requirements-build.txt`. Los resultados
quedan en `dist/release/`: `.deb`, `.tar.gz` y `SHA256SUMS`. Puedes elegir otro
directorio con `--output-dir`. La prueba del paquete extrae el `.deb` y ejecuta
ayuda, inicialización, ingesta de texto y búsqueda con configuración temporal;
no instala el paquete ni toca el archivo personal.

## Automatización de GitHub

`release.yml` construye y comprueba paquetes en las PR que afecten a su alcance
y mediante ejecución manual. Esos casos guardan artefactos en Actions, sin
publicar una release.

Solo un push de etiqueta con el formato `vX.Y.Z-rc.N`, que coincida exactamente
con `desktop/src-tauri/tauri.conf.json`, puede publicar una **prerelease**. Antes
de etiquetar, actualiza las versiones y `packaging/RELEASE_NOTES.md`; comprueba
el resultado de las pruebas. La publicación depende de que termine correctamente
la construcción y vuelve a verificar los checksums. Una etiqueta estable se
rechaza deliberadamente: este flujo no publica versiones estables.

El permiso de escritura y el token de publicación solo se conceden al trabajo
de publicación de etiquetas, nunca a una PR. Los paquetes no tienen firma
criptográfica independiente: `SHA256SUMS` permite comprobar su integridad frente
al archivo de sumas descargado, no sustituye una firma del autor.
