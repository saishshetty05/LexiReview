"""storage.py — document blob storage (MinIO/S3) and the RLS-scoped lookup
that resolves a document version to its blob (CONTRACTS.md §4).

put_document is DB-free: the documents row is written by the upload flow
elsewhere, and the blob key is fully derivable from (user_id, doc_id,
version), so no schema change or row lookup is needed to store a blob.

fetch_document is the read path: it resolves (doc_id, user_id,
doc_version_hash) to bytes, going through app_user_session so RLS -- not an
explicit user_id filter here -- is what scopes the lookup to the caller's
own rows. See CONTRACTS.md §4 for why the three error categories below are
structurally anti-enumeration, not just chosen to look that way.
"""
from __future__ import annotations

import logging
import os
import uuid

import boto3
from botocore.exceptions import ClientError
from sqlalchemy import select

from app.db import app_user_session
from app.models import Document

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Base for typed storage errors. category+message only -- never
    document content or a filename that might contain a party name
    (CLAUDE.md rule 2).
    """

    category: str

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DocumentNotFoundError(StorageError):
    category = "document_not_found"


class VersionMismatchError(StorageError):
    category = "version_mismatch"


class ObjectMissingError(StorageError):
    category = "object_missing"


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT"],
        aws_access_key_id=os.environ.get("MINIO_ROOT_USER"),
        aws_secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD"),
    )


def _bucket_name() -> str:
    return os.environ.get("S3_BUCKET", "documents")


def _document_key(user_id: uuid.UUID, doc_id: uuid.UUID, version: int) -> str:
    return f"{user_id}/{doc_id}/v{version}"


def _ensure_bucket(client, bucket: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 404:
            client.create_bucket(Bucket=bucket)
        else:
            raise


def put_document(user_id: uuid.UUID, doc_id: uuid.UUID, version: int, file_bytes: bytes) -> str:
    """Store `file_bytes` under the deterministic key for this version and
    return that key. Idempotent: safe to call again with the same
    (user_id, doc_id, version) (re-upload flows insert a new version row
    instead, per CLAUDE.md rule 9, so this is not expected to overwrite in
    practice).
    """
    client = _s3_client()
    bucket = _bucket_name()
    _ensure_bucket(client, bucket)
    key = _document_key(user_id, doc_id, version)
    client.put_object(Bucket=bucket, Key=key, Body=file_bytes)
    return key


def fetch_document(doc_id: uuid.UUID, user_id: uuid.UUID, doc_version_hash: str) -> bytes:
    """Resolve doc_id + doc_version_hash to the stored blob, scoped to
    user_id via RLS. Raises DocumentNotFoundError, VersionMismatchError, or
    ObjectMissingError -- see CONTRACTS.md §4 for the full decision table
    and why each case can only be reached the way it is.
    """
    with app_user_session(user_id) as session:
        rows = session.execute(select(Document).where(Document.doc_id == doc_id)).scalars().all()

    if not rows:
        raise DocumentNotFoundError("document not found")

    matching = [row for row in rows if row.doc_version_hash == doc_version_hash]
    if not matching:
        raise VersionMismatchError("document version hash does not match any known version")

    row = matching[0]
    key = _document_key(user_id, doc_id, row.version)
    client = _s3_client()
    bucket = _bucket_name()
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in ("NoSuchKey", "404"):
            logger.warning(
                "storage integrity alarm: object_missing doc_id=%s user_id=%s", doc_id, user_id
            )
            raise ObjectMissingError("stored object is missing") from exc
        raise
    return response["Body"].read()
