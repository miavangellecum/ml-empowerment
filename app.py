# backend/aws_clients.py
import boto3
import os

s3_client = boto3.client(
    "s3",
    region_name=os.getenv("AWS_REGION"),
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
)

BUCKET = os.getenv("S3_BUCKET_NAME")

def upload_file_to_s3(local_path: str, key: str) -> str:
    s3_client.upload_file(local_path, BUCKET, key)
    return f"s3://{BUCKET}/{key}"

def download_file_from_s3(key: str, local_path: str):
    s3_client.download_file(BUCKET, key, local_path)