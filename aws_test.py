import boto3, os
from dotenv import load_dotenv
load_dotenv()

s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION"))
print(s3.list_buckets())  # should list your bucket, confirms creds work

bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION"))
# a minimal invoke_model call here confirms model access is actually approved