from fastapi import APIRouter
from src.api.v1.endpoints import aws, ai, auth, chat, usage

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(chat.router, prefix="/chat", tags=["Chat Completion"])
api_router.include_router(usage.router, prefix="/usage", tags=["Usage & Monitoring"])
api_router.include_router(aws.router, prefix="/aws", tags=["AWS Services"])
api_router.include_router(ai.router, prefix="/ai", tags=["AI Services"])
