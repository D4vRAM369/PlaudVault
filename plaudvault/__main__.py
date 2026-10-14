"""__main__.py — Punto de entrada del CLI.

Uso:
    python -m plaudvault init                 # crea vault, schema y passphrase
    python -m plaudvault run                  # arranca el watcher (modo demonio)
    python -m plaudvault ingest <fichero>     # procesa un fichero suelto
    python -m plaudvault search "<consulta>"  # búsqueda full-text
    python -m plaudvault show 7               # muestra transcripción completa
    python -m plaudvault list                 # últimas grabaciones
    python -m plaudvault decrypt <id> [-o dir] # recupera un audio original

Por qué `python -m plaudvault` y no un script suelto: ejecutar el paquete
como módulo garantiza que los imports relativos funcionan igual en
cualquier máquina, sin pelearse con el PYTHONPATH.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import shutil
import sys
from pathlib import Path

from .config import load_config
from .crypto import WrongPassphrase, decrypt_file, init_vault_crypto, unlock
from .db import VaultDB
from .notifier import TelegramNotifier
from .pipeline import Pipeline
from .transcriber import Transcriber


def setup_logging(level: str, log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_file)],
    )


def ask_passphrase(confirm: bool = False) -> str:
    """getpass oculta lo que tecleas — la passphrase nunca aparece en
    pantalla ni en el historial de la shell."""
    p1 = getpass.getpass("Passphrase del vault: ")
    if confirm:
        p2 = getpass.getpass("Repítela: ")
        if p1 != p2:
            print("No coinciden. Aborto.", file=sys.stderr)
            sys.exit(1)
        if len(p1) < 8:
            print("Mínimo 8 caracteres.", file=sys.stderr)
            sys.exit(1)
    return p1


def get_key_or_exit(cfg) -> bytes | None:
    """Desbloquea el vault (si crypto está activo). Sale con error claro
    si la passphrase no corresponde."""
    if not cfg.crypto_enabled:
        return None
    if not cfg.salt_path.exists():
        print("Vault sin inicializar. Ejecuta: python -m plaudvault init",
              file=sys.stderr)
        sys.exit(1)
    try:
        return unlock(cfg.salt_path, cfg.keycheck_path, ask_passphrase())
    except WrongPassphrase as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)


def build_pipeline(cfg, key: bytes | None) -> tuple[Pipeline, VaultDB]:
    db = VaultDB(cfg.paths.db)
    db.init_schema()
    transcriber = Transcriber(
        cfg.whisper_model, cfg.language, cfg.device, cfg.compute_type
    )
    notifier = TelegramNotifier(
        cfg.telegram_token_env, cfg.telegram_chat_id_env, cfg.telegram_enabled
    )
    return Pipeline(cfg, db, transcriber, notifier, key), db


# ---------- subcomandos ----------

def cmd_init(cfg, _args) -> None:
    for p in (cfg.paths.base, cfg.paths.inbox, cfg.paths.archive):
        p.mkdir(parents=True, exist_ok=True)
    db = VaultDB(cfg.paths.db)
    db.init_schema()
    db.close()
    if cfg.crypto_enabled:
        if cfg.salt_path.exists():
            print("⚠️  El vault ya está inicializado. No se regenera el salt "
                  "(perderías acceso a los audios ya cifrados).")
        else:
            init_vault_crypto(cfg.salt_path, cfg.keycheck_path,
                              ask_passphrase(confirm=True))
            print("🔐 Crypto inicializado. Guarda bien esa passphrase: "
                  "sin ella, los audios cifrados son irrecuperables. "
                  "No hay puerta trasera — ese es el punto.")
    print(f"✅ Vault listo en {cfg.paths.base}")
    print(f"   Deja audios o transcripts en: {cfg.paths.inbox}")


def cmd_run(cfg, _args) -> None:
    from .watcher import run_watcher  # import tardío: watchdog solo aquí
    key = get_key_or_exit(cfg)
    pipeline, _db = build_pipeline(cfg, key)
    run_watcher(pipeline, cfg.paths.inbox)


def cmd_ingest(cfg, args) -> None:
    src = Path(args.file).expanduser().resolve()
    if not src.exists():
        print(f"No existe: {src}", file=sys.stderr)
        sys.exit(1)
    key = get_key_or_exit(cfg)
    pipeline, _db = build_pipeline(cfg, key)
    # Copiamos al inbox para que el flujo sea idéntico al del watcher
    # (y el original del usuario no se toca).
    cfg.paths.inbox.mkdir(parents=True, exist_ok=True)
    staged = cfg.paths.inbox / src.name
    shutil.copy2(src, staged)
    rec_id = pipeline.process_file(staged)
    if rec_id:
        print(f"✅ Ingerido como #{rec_id}")


def cmd_search(cfg, args) -> None:
    db = VaultDB(cfg.paths.db)
    rows = db.search(args.query, limit=args.limit)
    if not rows:
        print("Sin resultados.")
        return
    for r in rows:
        print(f"\n#{r['id']} · {r['filename']} · {r['ingested_at'][:10]} "
              f"({r['source_type']})")
        print(f"   {r['frag']}")
    db.close()


def cmd_list(cfg, _args) -> None:
    db = VaultDB(cfg.paths.db)
    for r in db.list_recordings():
        print(f"#{r['id']:4d}  {r['ingested_at'][:16]}  "
              f"[{r['source_type']:5s}]  {r['filename']}")
    db.close()


def cmd_show(cfg, args) -> None:
    db = VaultDB(cfg.paths.db)
    rec = db.get_transcript(args.id)
    db.close()
    if rec is None:
        print("No existe esa grabación o no tiene transcripción.", file=sys.stderr)
        sys.exit(1)
    print(f"#{rec['id']} · {rec['filename']} · {rec['ingested_at'][:10]}")
    print(f"Idioma: {rec['language']} · Modelo: {rec['model']}")
    print()
    print(rec["content"])


def cmd_sync(cfg, args) -> None:
    # Imports tardíos: requests/whisper solo cuando de verdad se usan.
    from .plaud_client import PlaudClient, PlaudError
    from .sync import PlaudSync

    try:
        client = PlaudClient.from_env()
    except PlaudError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)
    key = get_key_or_exit(cfg)
    db = VaultDB(cfg.paths.db)
    db.init_schema()
    transcriber = (Transcriber(cfg.whisper_model, cfg.language, cfg.device,
                               cfg.compute_type)
                   if args.transcribe_missing else None)
    try:
        done, skipped, failed = PlaudSync(cfg, db, client, key, transcriber).run(args.limit)
    except PlaudError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()
    print(f"✅ Importadas: {done} · ya existían: {skipped} · fallidas: {failed}")
    print(f"   Carpetas en: {cfg.paths.archive}")
    if failed:
        sys.exit(2)


def cmd_decrypt(cfg, args) -> None:
    db = VaultDB(cfg.paths.db)
    rec = db.get_recording(args.id)
    db.close()
    if rec is None or not rec["encrypted_path"]:
        print("No existe esa grabación o no tiene audio cifrado.", file=sys.stderr)
        sys.exit(1)
    key = get_key_or_exit(cfg)
    src = Path(rec["encrypted_path"])
    out_dir = Path(args.output).expanduser() if args.output else Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)
    from .sync import slug
    # Los títulos de Plaud pueden llevar "/" ("14/10 reunión"): sin sanear,
    # Path los interpreta como subcarpetas inexistentes.
    dst = out_dir / slug(rec["filename"], maxlen=200)
    if dst.exists():
        print(f"Ya existe {dst}; no lo sobrescribo. Bórralo o usa otro -o.",
              file=sys.stderr)
        sys.exit(1)
    decrypt_file(key, src, dst)
    print(f"🔓 Recuperado: {dst}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="plaudvault",
        description="PlaudVault — bóveda local cifrada para grabaciones de voz.",
    )
    parser.add_argument("-c", "--config", default="config.yaml",
                        help="ruta a config.yaml (por defecto: ./config.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="inicializa vault, schema y crypto")
    sub.add_parser("run", help="arranca el watcher sobre el inbox")

    p_ingest = sub.add_parser("ingest", help="procesa un fichero suelto")
    p_ingest.add_argument("file")

    p_search = sub.add_parser("search", help="búsqueda full-text (FTS5)")
    p_search.add_argument("query")
    p_search.add_argument("-n", "--limit", type=int, default=10)

    p_show = sub.add_parser("show", help="muestra la transcripción completa de un id")
    p_show.add_argument("id", type=int)

    sub.add_parser("list", help="últimas grabaciones")

    p_dec = sub.add_parser("decrypt", help="recupera el audio original de un id")
    p_dec.add_argument("id", type=int)
    p_dec.add_argument("-o", "--output", help="directorio de salida")

    p_sync = sub.add_parser("sync", help="importa grabaciones, notas e imágenes de Plaud")
    p_sync.add_argument("-n", "--limit", type=int,
                        help="máximo de grabaciones nuevas (útil para probar)")
    p_sync.add_argument("--transcribe-missing", action="store_true",
                        help="transcribe con whisper las que Plaud no transcribió")

    args = parser.parse_args()
    cfg = load_config(args.config)
    setup_logging(cfg.log_level, cfg.log_file)

    {
        "init": cmd_init,
        "run": cmd_run,
        "ingest": cmd_ingest,
        "search": cmd_search,
        "show": cmd_show,
        "list": cmd_list,
        "decrypt": cmd_decrypt,
        "sync": cmd_sync,
    }[args.command](cfg, args)


if __name__ == "__main__":
    main()
