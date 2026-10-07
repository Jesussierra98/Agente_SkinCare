"""Empaqueta las dos Lambdas en `out/lambda/etl.zip` y `out/lambda/caja.zip`.

Uso:  python scripts/package_lambdas.py [carpeta_salida]

- ETL: `handler.py`, `pipeline.py`, `sinks.py`, `core/` y la configuración versionada (sin `guide.json`, que solo
  usa el agente), más las dependencias de `src/etl/requirements.txt` instaladas con pip (ftfy y wcwidth son Python puro,
  así que sirven para arm64 y x86_64).
- Caja: `caja_core.py` y `caja_handler.py` (solo usa boto3, que ya viene en el runtime de Lambda).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ETL = ROOT / "src" / "etl"
CAJA = ROOT / "src" / "caja_api"

ETL_FILES = ["handler.py", "pipeline.py", "sinks.py"]
ETL_DIRS = ["core"]
ETL_CONFIG_EXCLUDE = {"guide.json"}
CAJA_FILES = ["caja_core.py", "caja_handler.py"]


def _ignore(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n == "__pycache__" or n.endswith(".pyc")}


def zip_directory(source: Path, target: Path) -> None:
    """Zip determinista: rutas relativas con `/`, orden alfabético y fecha fija."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            info = zipfile.ZipInfo(path.relative_to(source).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())


def build_etl(out_dir: Path, install_dependencies: bool = True) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        if install_dependencies:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", "--no-compile", "-r", str(ETL / "requirements.txt"), "-t", str(stage)],
                check=True,
            )
        for name in ETL_FILES:
            shutil.copy2(ETL / name, stage / name)
        for name in ETL_DIRS:
            shutil.copytree(ETL / name, stage / name, ignore=_ignore, dirs_exist_ok=True)
        config = stage / "config"
        config.mkdir(exist_ok=True)
        for path in (ETL / "config").glob("*.json"):
            if path.name not in ETL_CONFIG_EXCLUDE:
                shutil.copy2(path, config / path.name)
        target = out_dir / "etl.zip"
        zip_directory(stage, target)
    return target


def build_caja(out_dir: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        for name in CAJA_FILES:
            shutil.copy2(CAJA / name, stage / name)
        target = out_dir / "caja.zip"
        zip_directory(stage, target)
    return target


def main(argv: list[str]) -> int:
    out_dir = Path(argv[1]) if len(argv) > 1 else ROOT / "out" / "lambda"
    for target in (build_etl(out_dir), build_caja(out_dir)):
        print(f"{target}  ({target.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
