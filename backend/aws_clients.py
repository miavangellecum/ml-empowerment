import boto3
import os
from botocore.exceptions import ClientError

s3_client = boto3.client(
    "s3",
    region_name=os.getenv("AWS_REGION"),
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
)

S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

def upload_file_to_s3(local_path: str, key: str) -> str:
    try:
        s3_client.upload_file(local_path, S3_BUCKET_NAME, key)
        return f"s3://{S3_BUCKET_NAME}/{key}"
    except ClientError as e:
        print(f"Failed to upload to S3: {e}")
        raise

def upload_file_to_s3_from_bytes(file_bytes: bytes, key: str) -> str:
    """Upload file bytes directly to S3 without writing to disk (serverless-compatible)."""
    try:
        s3_client.put_object(Bucket=S3_BUCKET_NAME, Key=key, Body=file_bytes)
        return f"s3://{S3_BUCKET_NAME}/{key}"
    except ClientError as e:
        print(f"Failed to upload bytes to S3: {e}")
        raise

def download_file_from_s3(key: str, local_path: str):
    try:
        s3_client.download_file(S3_BUCKET_NAME, key, local_path)
    except ClientError as e:
        print(f"Failed to download from S3: {e}")
        raise

def get_presigned_url(s3_key: str, expires_in: int = 3600) -> str:
    """Generate a presigned URL for an S3 object."""
    try:
        # Make sure the key doesn't have a leading slash
        key = s3_key.lstrip('/')
        url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET_NAME, "Key": key},
            ExpiresIn=expires_in,
        )
        return url
    except ClientError as e:
        print(f"Failed to generate presigned URL for {s3_key}: {e}")
        raise