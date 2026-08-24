import logging
from functools import lru_cache

import boto3
from botocore.client import Config

from app.config import Settings

logger = logging.getLogger(__name__)


@lru_cache
def _client(endpoint: str, region: str, access_key: str, secret_key: str):
    # path-style giống cấu hình S3 của BE (StorageConfig dùng pathStyleAccessEnabled).
    return boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


class DocumentStorage:
    """Bucket RIÊNG của AI service cho tài liệu RAG — không dùng chung với bucket của BE."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._enabled = bool(settings.s3_access_key and settings.s3_secret_key)
        if not self._enabled:
            logger.warning("Chưa cấu hình S3 — file gốc sẽ không được lưu, chỉ index nội dung")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _s3(self):
        s = self._settings
        return _client(s.s3_endpoint, s.s3_region, s.s3_access_key, s.s3_secret_key)

    def put(self, key: str, data: bytes, content_type: str | None) -> str | None:
        if not self._enabled:
            return None
        self._s3().put_object(
            Bucket=self._settings.s3_bucket,
            Key=key,
            Body=data,
            ContentType=content_type or "application/octet-stream",
        )
        return key

    def get(self, key: str) -> bytes:
        obj = self._s3().get_object(Bucket=self._settings.s3_bucket, Key=key)
        return obj["Body"].read()

    def delete(self, key: str) -> None:
        if not self._enabled:
            return
        try:
            self._s3().delete_object(Bucket=self._settings.s3_bucket, Key=key)
        except Exception:  # noqa: BLE001
            logger.exception("Xóa file %s thất bại", key)

    def presigned_url(self, key: str, expires: int = 900) -> str | None:
        if not self._enabled:
            return None
        return self._s3().generate_presigned_url(
            "get_object",
            Params={"Bucket": self._settings.s3_bucket, "Key": key},
            ExpiresIn=expires,
        )
