# PlaudVault v0.3.0-rc.1 — primera distribución Linux

Primera versión candidata con instalador y escritorio **Bóveda nocturna**.

## Descargas

- **Ubuntu 24.04 x86_64:** `plaudvault_0.3.0~rc1_amd64.deb`.
- **Portable:** `PlaudVault-0.3.0-rc.1-linux-x86_64.tar.gz`.
- `SHA256SUMS` para comprobar la integridad.

Incluyen Python, la CLI, el frontend y el lanzador Tauri. El sistema necesita WebKitGTK, GTK, bubblewrap y los componentes multimedia declarados; `apt` resuelve esas dependencias al instalar el `.deb`.

## Instalar y empezar

```bash
sha256sum --ignore-missing --check SHA256SUMS
sudo apt install ./plaudvault_0.3.0~rc1_amd64.deb
plaudvault init
plaudvault desktop
```

Guarda la passphrase: sin ella no puedes recuperar el audio cifrado. Encontrarás también PlaudVault en el menú de aplicaciones.

## Novedades

- GUI Tauri + Svelte de solo lectura para navegar, buscar, leer y escuchar.
- Grabaciones locales y de Plaud visibles en ambas interfaces.
- Búsqueda CLI robusta frente a signos y consultas sin tildes.
- Arranque verificado con perfil en RAM y renderer en sandbox.
- CLI incluida para importar archivos, sincronizar Plaud y gestionar el archivo.

## Límites de esta candidata

Solo se cifra el **audio**; transcripciones y notas permanecen en claro. No protege contra malware activo en la sesión. Sin swap cifrado, la RAM puede acabar en disco. La configuración medida no garantiza cero rastro en todas las distribuciones o versiones de WebKitGTK.

La GUI no obtiene el token ni ejecuta `sync`: se gestionan desde la CLI. Los modelos Whisper no están incluidos y pueden descargarse en la primera transcripción. Compatibilidad inicial: Ubuntu 24.04 x86_64; otras plataformas no están verificadas.

Proyecto no oficial, sin relación con Plaud. Usa una API web no documentada que puede cambiar.
