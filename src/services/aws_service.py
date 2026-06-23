import boto3
from botocore.exceptions import ClientError
from typing import Dict, Any, Optional
from src.core.config import settings

class AWSService:
    def __init__(self):
        self.s3_client = None
        # Only initialize if credentials are provided and not placeholders
        has_credentials = (
            settings.AWS_ACCESS_KEY_ID and 
            settings.AWS_ACCESS_KEY_ID != "placeholder_key" and
            settings.AWS_SECRET_ACCESS_KEY and
            settings.AWS_SECRET_ACCESS_KEY != "placeholder_secret"
        )
        if has_credentials:
            try:
                self.s3_client = boto3.client(
                    "s3",
                    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                    region_name=settings.AWS_REGION
                )
            except Exception as e:
                print(f"Failed to initialize AWS S3 client: {e}")
        else:
            print("AWS credentials not fully configured. S3 client runs in mock mode.")

    def upload_file(self, file_content: bytes, object_name: str, bucket_name: Optional[str] = None) -> Dict[str, Any]:
        """Upload a file to an S3 bucket."""
        bucket = bucket_name or settings.AWS_BUCKET_NAME
        if not bucket:
            return {"success": False, "error": "No S3 Bucket specified"}

        if not self.s3_client:
            # Mock implementation when credentials aren't set
            return {
                "success": True, 
                "message": f"[Mock Mode] Successfully uploaded {object_name} ({len(file_content)} bytes) to bucket {bucket}"
            }

        try:
            self.s3_client.put_object(
                Bucket=bucket,
                Key=object_name,
                Body=file_content
            )
            return {"success": True, "message": f"Successfully uploaded {object_name} to bucket {bucket}"}
        except ClientError as e:
            return {"success": False, "error": str(e)}

    def get_file_url(self, object_name: str, bucket_name: Optional[str] = None, expiration: int = 3600) -> Dict[str, Any]:
        """Generate a presigned URL to share an S3 object."""
        bucket = bucket_name or settings.AWS_BUCKET_NAME
        if not bucket:
            return {"success": False, "error": "No S3 Bucket specified"}

        if not self.s3_client:
            # Mock implementation when credentials aren't set
            return {
                "success": True, 
                "url": f"https://{bucket}.s3.{settings.AWS_REGION}.amazonaws.com/{object_name}?mock=true"
            }

        try:
            response = self.s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": object_name},
                ExpiresIn=expiration
            )
            return {"success": True, "url": response}
        except ClientError as e:
            return {"success": False, "error": str(e)}

aws_service = AWSService()
