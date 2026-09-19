import boto3
from botocore.exceptions import ClientError

from vista.config import settings


def s3_client():
    """S3 client for MinIO locally (explicit endpoint + static keys) or AWS S3
    (no endpoint, credentials from the task/instance IAM role)."""
    kwargs: dict = {}
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    if settings.s3_access_key and settings.s3_secret_key:
        kwargs["aws_access_key_id"] = settings.s3_access_key
        kwargs["aws_secret_access_key"] = settings.s3_secret_key
    if settings.s3_region:
        kwargs["region_name"] = settings.s3_region
    return boto3.client("s3", **kwargs)


def ensure_bucket() -> None:
    client = s3_client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
        return
    except ClientError as exc:
        # Only create when the bucket is genuinely missing; a 403 means it exists
        # but this identity cannot see it, which must surface as an error.
        if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket"):
            raise
    region = settings.s3_region or client.meta.region_name
    params: dict = {"Bucket": settings.s3_bucket}
    if region and region != "us-east-1" and not settings.s3_endpoint_url:
        params["CreateBucketConfiguration"] = {"LocationConstraint": region}
    client.create_bucket(**params)


def presigned_upload_url(key: str, content_type: str, expires: int = 900) -> str:
    return s3_client().generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.s3_bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=expires,
    )


def presigned_download_url(key: str, expires: int = 900) -> str:
    return s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=expires,
    )
