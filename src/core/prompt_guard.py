"""
Prompt Guard — Pre-Inference Security Layer
============================================
Filters malicious prompts BEFORE they reach the GPU/LLM.

Security pipeline:
    JWT → Redis Blacklist → API Key → Rate Limit
    → **Prompt Injection Filter** → **PII Detection**
    → Inference Router → Model

This module runs BEFORE any compute — if a prompt is flagged,
zero GPU cycles are wasted.
"""

import re
import logging
from fastapi import HTTPException, status
from typing import Optional

logger = logging.getLogger("security.prompt_guard")

# ─── Compiled regex patterns (compiled once at import time) ──────────────────

PROMPT_INJECTION_PATTERNS = [
    # Direct instruction override attempts
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"ignore\s+(all\s+)?prior\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?previous", re.IGNORECASE),
    re.compile(r"forget\s+(all\s+)?previous", re.IGNORECASE),

    # System prompt extraction
    re.compile(r"(show|reveal|display|print|output)\s+(me\s+)?(the\s+)?system\s+prompt", re.IGNORECASE),
    re.compile(r"what\s+(is|are)\s+(your|the)\s+system\s+(prompt|instructions)", re.IGNORECASE),

    # Role hijacking
    re.compile(r"you\s+are\s+now\s+a", re.IGNORECASE),
    re.compile(r"act\s+as\s+if\s+you\s+(are|were)\s+a", re.IGNORECASE),
    re.compile(r"pretend\s+(you\s+are|to\s+be)", re.IGNORECASE),

    # Settings override
    re.compile(r"override\s+(your\s+)?settings", re.IGNORECASE),
    re.compile(r"change\s+(your\s+)?rules", re.IGNORECASE),
    re.compile(r"bypass\s+(your\s+)?(safety|content|filter)", re.IGNORECASE),

    # Jailbreak patterns
    re.compile(r"DAN\s+mode", re.IGNORECASE),
    re.compile(r"developer\s+mode\s+(enabled|on|activated)", re.IGNORECASE),
]

# PII detection patterns
PII_PATTERNS = {
    "email": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "phone": re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
}


def inspect_prompt_safety(prompt: str) -> None:
    """
    Check the prompt for injection attacks.

    Raises HTTPException(400) if a pattern matches.
    Must be called BEFORE any inference — zero GPU wasted on attacks.
    """
    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern.search(prompt):
            logger.warning(
                "[PROMPT GUARD] Injection detected: pattern=%s  snippet=%s",
                pattern.pattern[:50], prompt[:80],
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Security violation: Potential prompt injection detected.",
            )


def detect_pii(prompt: str) -> dict:
    """
    Scan the prompt for PII patterns.

    Returns a dict of detected PII types and their counts.
    Does NOT block — just reports. Use for audit logging.
    """
    detections = {}
    for pii_type, pattern in PII_PATTERNS.items():
        matches = pattern.findall(prompt)
        if matches:
            detections[pii_type] = len(matches)
    if detections:
        logger.info("[PROMPT GUARD] PII detected: %s", detections)
    return detections


def sanitize_pii(prompt: str) -> str:
    """
    Redact detected PII from the prompt.

    Replaces PII with type-specific placeholders:
        email       → [EMAIL_REDACTED]
        phone       → [PHONE_REDACTED]
        ssn         → [SSN_REDACTED]
        credit_card → [CC_REDACTED]
    """
    redaction_map = {
        "email": "[EMAIL_REDACTED]",
        "phone": "[PHONE_REDACTED]",
        "ssn": "[SSN_REDACTED]",
        "credit_card": "[CC_REDACTED]",
    }
    sanitized = prompt
    for pii_type, pattern in PII_PATTERNS.items():
        sanitized = pattern.sub(redaction_map[pii_type], sanitized)
    return sanitized
