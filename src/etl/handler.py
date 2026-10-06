"""ETL_Lambda: se dispara con `ObjectCreated` en `raw/*.csv`.

Variables de entorno: PRODUCTS_TABLE, KB_BUCKET, KB_ID, KB_DATA_SOURCE_ID.
Configuración de la función: 1024 MB, timeout 300 s, MaximumRetryAttempts 0 (un reintento
automático reprocesaría el CSV y lanzaría otro StartIngestionJob).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

from core.product import load_config
from pipeline import EtlFailed, run_etl
from sinks import AwsSink

logging.getLogger().setLevel(logging.INFO)
log = logging.getLogger("etl.handler")

CONFIG_DIR = Path(__file__).resolve().parent / "config"


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    import boto3

    results = []
    s3 = boto3.client("s3")
    cfg = load_config(CONFIG_DIR)
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
        if not key.startswith("raw/") or not key.lower().endswith(".csv"):
            log.info("objeto ignorado: s3://%s/%s", bucket, key)
            continue
        log.info("procesando s3://%s/%s", bucket, key)
        data = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        sink = AwsSink(
            products_table=os.environ.get("PRODUCTS_TABLE", "ultra-productos"),
            kb_bucket=os.environ.get("KB_BUCKET", "ultra-skincare-kb-source"),
            output_bucket=bucket,
            knowledge_base_id=os.environ["KB_ID"],
            data_source_id=os.environ["KB_DATA_SOURCE_ID"],
            s3=s3,
        )
        # UnreadableCsv y EtlFailed se propagan: la ejecución queda en estado de falla.
        report = run_etl(data, Path(key).name, cfg, sink)
        results.append({"file": key, "written": len(report.written), "omitted": len(report.omitted)})
    return {"processed": results}


__all__ = ["handler", "EtlFailed"]
