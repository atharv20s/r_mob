"""
Conversation Management Endpoints
===================================
CRUD operations for ChatGPT-style conversation threads.

Each user can have multiple independent conversations, each identified
by a UUID conversation_id. Conversation history is stored in Redis:
    conversation:{conversation_id}  →  LIST (capped, 24h TTL)
    user:{user_id}:conversations    →  SET of conversation_ids

Endpoints:
    POST   /api/v1/conversations              — create a new conversation
    GET    /api/v1/conversations              — list user's conversations
    GET    /api/v1/conversations/{id}         — get conversation history
    DELETE /api/v1/conversations/{id}         — clear/delete a conversation
"""

import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from src.core.deps import get_current_user
from src.core.schemas import UserSession
from src.services.redis_service import redis_service

router = APIRouter()


class CreateConversationRequest(BaseModel):
    title: Optional[str] = None


class ConversationInfo(BaseModel):
    conversation_id: str
    title: str


class ConversationHistory(BaseModel):
    conversation_id: str
    messages: List[Dict[str, Any]]
    message_count: int


@router.post("", response_model=ConversationInfo, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: CreateConversationRequest = CreateConversationRequest(),
    user: UserSession = Depends(get_current_user),
):
    """Create a new conversation thread and return its ID."""
    conversation_id = str(uuid.uuid4())
    title = payload.title or f"Chat {conversation_id[:8]}"

    # Link conversation to user
    redis_service.link_conversation_to_user(user.id, conversation_id, title=title)

    return ConversationInfo(
        conversation_id=conversation_id,
        title=title,
    )


@router.get("", response_model=List[ConversationInfo])
def list_conversations(
    user: UserSession = Depends(get_current_user),
):
    """List all conversations for the current user."""
    conversations = redis_service.get_user_conversations(user.id)
    return [
        ConversationInfo(
            conversation_id=conv_id,
            title=title,
        )
        for conv_id, title in conversations
    ]


@router.get("/{conversation_id}", response_model=ConversationHistory)
def get_conversation(
    conversation_id: str,
    user: UserSession = Depends(get_current_user),
):
    """Get the full message history for a conversation."""
    # Verify ownership
    if not redis_service.user_owns_conversation(user.id, conversation_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

    messages = redis_service.get_conversation_history(conversation_id)
    return ConversationHistory(
        conversation_id=conversation_id,
        messages=messages,
        message_count=len(messages),
    )


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    user: UserSession = Depends(get_current_user),
):
    """Delete a conversation and all its history."""
    if not redis_service.user_owns_conversation(user.id, conversation_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

    redis_service.clear_conversation(conversation_id)
    redis_service.unlink_conversation_from_user(user.id, conversation_id)

    return {"message": f"Conversation {conversation_id} deleted."}
