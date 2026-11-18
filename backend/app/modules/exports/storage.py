"""Private S3-compatible artifact storage and time-limited download URLs."""

from __future__ import annotations

from dataclasses import dataclass

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import Settings


class ObjectStorageConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StoredObject:
    bucket: str
    key: str


class S3ArtifactStore:
    def __init__(self, settings: Settings) -> None:
        if not settings.STORAGE_ACCESS_KEY or not settings.STORAGE_SECRET_KEY:
            raise ObjectStorageConfigurationError(
                "STORAGE_ACCESS_KEY and STORAGE_SECRET_KEY are required for exports"
            )
        self.bucket = settings.STORAGE_BUCKET
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.STORAGE_ENDPOINT,
            region_name=settings.STORAGE_REGION,
            aws_access_key_id=settings.STORAGE_ACCESS_KEY,
            aws_secret_access_key=settings.STORAGE_SECRET_KEY,
            use_ssl=settings.STORAGE_USE_SSL,
            config=Config(signature_version="s3v4"),
        )

    def ensure_private_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
            if status not in {400, 404}:
                raise
            self._client.create_bucket(Bucket=self.bucket)
        # An empty bucket policy means anonymous access has no allow rule in MinIO/S3.
        # Do not attach an ACL to objects; modern S3-compatible stores default private.

    def put_pdf(self, *, key: str, content: bytes) -> StoredObject:
        self.ensure_private_bucket()
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType="application/pdf",
            ContentDisposition='attachment; filename="paper.pdf"',
            CacheControl="private, no-store",
        )
        return StoredObject(bucket=self.bucket, key=key)

    def put_docx(
        self, *, key: str, content: bytes, artifact_type: str
    ) -> StoredObject:
        self.ensure_private_bucket()
        filename = "answer-key.docx" if artifact_type == "answer_key" else "paper.docx"
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            ContentDisposition=f'attachment; filename="{filename}"',
            CacheControl="private, no-store",
        )
        return StoredObject(bucket=self.bucket, key=key)

    def signed_get_url(self, *, key: str, expires_seconds: int) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )


__all__ = [
    "ObjectStorageConfigurationError",
    "S3ArtifactStore",
    "StoredObject",
]
