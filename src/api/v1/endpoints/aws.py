from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from typing import Optional
from src.services.aws_service import aws_service

router = APIRouter()

@router.post("/upload")
async def upload_to_s3(
    file: UploadFile = File(...),
    bucket_name: Optional[str] = Form(None)
):
    """Upload a file to S3 (runs in mock mode if keys are not set)."""
    try:
        content = await file.read()
        res = aws_service.upload_file(
            file_content=content,
            object_name=file.filename,
            bucket_name=bucket_name
        )
        if not res["success"]:
            raise HTTPException(status_code=400, detail=res["error"])
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/url")
def get_s3_url(
    object_name: str,
    bucket_name: Optional[str] = None,
    expiration: int = 3600
):
    """Generate a presigned S3 url for downloading an object."""
    res = aws_service.get_file_url(
        object_name=object_name,
        bucket_name=bucket_name,
        expiration=expiration
    )
    if not res["success"]:
        raise HTTPException(status_code=400, detail=res["error"])
    return res
