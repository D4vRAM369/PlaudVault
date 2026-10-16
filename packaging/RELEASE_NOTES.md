**Español** · [English](#english)

# PlaudVault v0.3.1 — icono de ventana

Versión de mantenimiento sobre la 0.3.0: corrige el icono del escritorio y amplía la documentación.

## Descargas

- **Ubuntu 24.04 x86_64:** `plaudvault_0.3.1_amd64.deb`.
- **Portable:** `PlaudVault-0.3.1-linux-x86_64.tar.gz`.
- `SHA256SUMS` para comprobar la integridad.

## Actualizar o instalar

```bash
sha256sum --ignore-missing --check SHA256SUMS
sudo apt install ./plaudvault_0.3.1_amd64.deb
```

Si ya tienes la 0.3.0, el `.deb` la actualiza sin tocar tu vault (`~/PlaudVault`). Instalación nueva: `plaudvault init` y luego `plaudvault desktop`.

## Novedades

- **Icono real en la barra de tareas y en Alt+Tab.** La ventana mostraba un cuadrado verde provisional; ahora lleva el icono de PlaudVault a 256 px y la entrada del menú declara `StartupWMClass`, así que el panel asocia la ventana con su lanzador.
- **README en inglés** con selector de idioma.
- **Privacidad de los metadatos documentada:** fecha, hora y título quedan en claro en los nombres de carpeta, y duración y carpeta de Plaud en `vault.db`. Para cubrirlos, usa LUKS o un contenedor VeraCrypt.
- **Roadmap:** la integración con Obsidian aparece como posible línea futura, sin fecha.

## Límites de esta versión

Sin cambios respecto a la 0.3.0. Solo se cifra el **audio**; transcripciones, notas y metadatos permanecen en claro. No protege contra malware activo en la sesión. Sin swap cifrado, la RAM puede acabar en disco. Compatibilidad inicial: Ubuntu 24.04 x86_64.

Proyecto no oficial, sin relación con Plaud. Usa una API web no documentada que puede cambiar.

---

<a id="english"></a>

[Español](#) · **English**

# PlaudVault v0.3.1 — window icon

Maintenance release on top of 0.3.0: fixes the desktop icon and extends the documentation.

## Downloads

- **Ubuntu 24.04 x86_64:** `plaudvault_0.3.1_amd64.deb`.
- **Portable:** `PlaudVault-0.3.1-linux-x86_64.tar.gz`.
- `SHA256SUMS` to check integrity.

## Upgrade or install

```bash
sha256sum --ignore-missing --check SHA256SUMS
sudo apt install ./plaudvault_0.3.1_amd64.deb
```

If you already have 0.3.0, the `.deb` upgrades it without touching your vault (`~/PlaudVault`). Fresh install: `plaudvault init`, then `plaudvault desktop`.

## What's new

- **Real icon in the taskbar and Alt+Tab.** The window showed a placeholder teal square; it now carries the PlaudVault icon at 256 px and the menu entry declares `StartupWMClass`, so the panel ties the window to its launcher.
- **English README** with a language selector.
- **Metadata privacy documented:** date, time and title stay in plain text in folder names, and duration and Plaud folder in `vault.db`. To cover them, use LUKS or a VeraCrypt container.
- **Roadmap:** Obsidian integration is listed as a possible future direction, with no date.

## Limits of this version

Unchanged from 0.3.0. Only the **audio** is encrypted; transcripts, notes and metadata stay in plain text. It does not protect against active malware in the session. Without encrypted swap, RAM may end up on disk. Initial compatibility: Ubuntu 24.04 x86_64.

Unofficial project, not affiliated with Plaud. It uses an undocumented web API that may change.
