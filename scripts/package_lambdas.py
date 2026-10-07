"""Empaqueta las dos Lambdas (ETL y Caja) en .zip para `scripts/deploy.ps1`.

- ETL: `src/etl` (handler, núcleo, configuración) más sus dependencias (`ftfy`, `wcwidth`: Python puro).
- Caja: `caja_core.py` y `caja_handler.py` (boto3 ya viene en el runtime de Lambda).

Las rutas dentro del .zip usan barra normal (un .zip hecho con `Compress-Archive` de PowerShell 5 usa barra invertida y
Lambda no lo lee bien).

Uso:  python scripts/package_lambdas.py out/build
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ETL_DIR = ROOT / "src" / "etl"
CAJA_DIR = ROOT / "src" / "caja_api"
IGNORED_PARTS = {"__pycache__", ".pytest_cache", "node_modules"}
# Solo lo que el ETL necesita en producción: el CSV del catálogo y el Excel de la guía NO se empaquetan.
ETL_EXCLUDED_SUFFIXES = {".csv", ".xlsx", ".pyc"}


def _include(path: Path, excluded_suffixes: set[str]) -> bool:
    return path.is_file() and not (set(path.parts) & IGNORED_PARTS) and path.suffix not in excluded_suffixes


def collect_etl_files() -> dict[str, Path]:
    """Ruta dentro del .zip → archivo. Incluye `handler.py`, `pipeline.py`, `sinks.py`, `core/` y `config/`."""
    files: dict[str, Path] = {}
    for path in sorted(ETL_DIR.rglob("*")):
        if _include(path, ETL_EXCLUDED_SUFFIXES) and path.name != "requirements.txt":
            files[path.relative_to(ETL_DIR).as_posix()] = path
    return files


def collect_caja_files() -> dict[str, Path]:
    return {p.name: p for p in sorted(CAJA_DIR.glob("*.py"))}


def write_zip(entries: dict[str, Path], destination: Path, extra_dir: Path | None = None) -> str:
    """Escribe el .zip y devuelve su SHA-256 (sirve para que la clave de S3 cambie solo cuando cambia el código)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    all_entries = dict(entries)
    if extra_dir is not None:
        for path in sorted(extra_dir.rglob("*")):
            if _include(path, {".pyc"}):
                all_entries.setdefault(path.relative_to(extra_dir).as_posix(), path)
    digest = hashlib.sha256()
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(all_entries):
            data = all_entries[name].read_bytes()
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))  # fecha fija: el mismo código da el mismo hash
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
            digest.update(name.encode() + b"\0" + data)
    return digest.hexdigest()


def install_etl_dependencies(target: Path) -> None:
    """Instala las dependencias del ETL para Linux x86_64 y Python 3.12, sin importar en qué sistema se empaquete."""
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "--quiet", "--requirement", str(ETL_DIR / "requirements.txt"),
            "--target", str(target),
            "--platform", "manylinux2014_x86_64", "--python-version", "3.12", "--only-binary=:all:",
        ],
        check=True,
    )


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else ROOT / "out" / "build"
    deps = out / "etl-deps"
    install_etl_dependencies(deps)
    etl_hash = write_zip(collect_etl_files(), out / "etl.zip", extra_dir=deps)
    caja_hash = write_zip(collect_caja_files(), out / "caja.zip")
    print(f"etl.zip   {etl_hash[:12]}  ({len(collect_etl_files())} archivos propios)")
    print(f"caja.zip  {caja_hash[:12]}  ({len(collect_caja_files())} archivos)")
    (out / "hashes.txt").write_text(f"etl={etl_hash[:12]}\ncaja={caja_hash[:12]}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
