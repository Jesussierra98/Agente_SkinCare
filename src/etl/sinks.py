"""Destinos del ETL: disco (desarrollo local) y AWS (S3 + DynamoDB + Bedrock Knowledge Base)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.kb import kb_document_key, kb_metadata_key
from core.product import Product

log = logging.getLogger("etl.sinks")


class LocalSink:
    """Escribe todo en una carpeta local. No hay base de datos ni ingesta."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        (out_dir / "productos").mkdir(parents=True, exist_ok=True)
        (out_dir / "normalized").mkdir(parents=True, exist_ok=True)

    def write_product(self, product: Product, markdown: str, metadata: dict) -> None:
        (self.out_dir / kb_document_key(product.sku)).write_text(markdown, encoding="utf-8")
        (self.out_dir / kb_metadata_key(product.sku)).write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )

    def write_normalized(self, filename: str, data: bytes) -> None:
        (self.out_dir / "normalized" / Path(filename).name).write_bytes(data)

    def start_ingestion(self) -> None:
        log.info("ingesta de la Knowledge Base omitida (ejecución local)")


class AwsSink:
    """`ultra-productos` (DynamoDB), bucket de la KB (S3) y `StartIngestionJob` (Bedrock)."""

    def __init__(
        self,
        *,
        products_table: str,
        kb_bucket: str,
        output_bucket: str,
        knowledge_base_id: str,
        data_source_id: str,
        dynamodb: Any = None,
        s3: Any = None,
        bedrock_agent: Any = None,
    ) -> None:
        import boto3  # import diferido: el modo local no lo necesita

        self._table = (dynamodb or boto3.resource("dynamodb")).Table(products_table)
        self._s3 = s3 or boto3.client("s3")
        self._agent = bedrock_agent or boto3.client("bedrock-agent")
        self._kb_bucket = kb_bucket
        self._output_bucket = output_bucket
        self._kb_id = knowledge_base_id
        self._ds_id = data_source_id

    def write_product(self, product: Product, markdown: str, metadata: dict) -> None:
        # PutItem reemplaza el registro completo: reprocesar un CSV no duplica nada.
        self._table.put_item(Item=product.to_item())
        self._s3.put_object(
            Bucket=self._kb_bucket,
            Key=kb_document_key(product.sku),
            Body=markdown.encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
        )
        self._s3.put_object(
            Bucket=self._kb_bucket,
            Key=kb_metadata_key(product.sku),
            Body=json.dumps(metadata, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )

    def write_normalized(self, filename: str, data: bytes) -> None:
        # Fuera del prefijo `raw/` para no disparar de nuevo el evento de S3.
        self._s3.put_object(
            Bucket=self._output_bucket,
            Key=f"normalized/{Path(filename).name}",
            Body=data,
            ContentType="text/csv; charset=utf-8",
        )

    def start_ingestion(self) -> None:
        self._agent.start_ingestion_job(knowledgeBaseId=self._kb_id, dataSourceId=self._ds_id)
