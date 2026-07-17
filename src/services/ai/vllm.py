"""
vLLM LLM Adapter
================
Connects to a vLLM inference server via its OpenAI-compatible REST API.

Default endpoint:
    Docker:  http://vllm-cluster:8000
    Local:   http://localhost:8001

vLLM exposes an OpenAI-compatible /v1/completions endpoint.
Conforms to BaseInferenceAdapter interface.
"""

import json
import httpx
import asyncio
import logging
from typing import Dict, Any, Optional, List, AsyncGenerator

from src.core.config import settings
from src.services.ai.base import BaseInferenceAdapter

logger = logging.getLogger("ai.vllm")


class VLLMAdapter(BaseInferenceAdapter):
    """Adapter for the vLLM high-throughput inference server."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or getattr(settings, "VLLM_BASE_URL", "http://vllm-cluster:8000")).rstrip("/")
        self.provider_name = "vllm"

    async def generate(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.7,
        max_retries: int = 3,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Generate a completion from vLLM via its OpenAI-compatible API.
        """
        model = model_name or "meta-llama/Llama-3-8b"
        
        # Build the full prompt from conversation history if provided
        if messages:
            compiled_parts = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                compiled_parts.append(f"{role}: {content}")
            full_prompt = "\n".join(compiled_parts)
        else:
            full_prompt = prompt

        payload = {
            "model": model,
            "prompt": full_prompt,
            "max_tokens": 500,
            "temperature": temperature,
        }

        backoff_factor = 2.0
        timeout = httpx.Timeout(timeout=120.0, connect=10.0)

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        f"{self.base_url}/v1/completions",
                        json=payload,
                    )

                if response.status_code == 429:
                    sleep_time = backoff_factor ** attempt
                    logger.warning(
                        "vLLM rate limit (429). Retrying in %.1fs...", sleep_time
                    )
                    await asyncio.sleep(sleep_time)
                    continue

                if response.status_code >= 500:
                    sleep_time = backoff_factor ** attempt
                    logger.warning(
                        "vLLM server error (%d). Retrying in %.1fs...",
                        response.status_code, sleep_time,
                    )
                    await asyncio.sleep(sleep_time)
                    continue

                response.raise_for_status()
                data = response.json()

                choices = data.get("choices", [])
                text = choices[0].get("text", "") if choices else ""
                usage = data.get("usage", {})

                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

                return {
                    "success": True,
                    "text": text,
                    "tokens_used": total_tokens,
                    "provider": self.provider_name,
                    "model": data.get("model", model),
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                    },
                }

            except httpx.TimeoutException:
                sleep_time = backoff_factor ** attempt
                logger.warning(
                    "vLLM timeout (attempt %d/%d). Retrying in %.1fs...",
                    attempt + 1, max_retries, sleep_time,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(sleep_time)
                else:
                    return {
                        "success": False,
                        "error": "vLLM request timed out after all retries",
                        "provider": self.provider_name,
                    }
            except httpx.ConnectError:
                return {
                    "success": False,
                    "error": f"Cannot connect to vLLM at {self.base_url}. Is it running?",
                    "provider": self.provider_name,
                }
            except Exception as exc:
                return {
                    "success": False,
                    "error": str(exc),
                    "provider": self.provider_name,
                }

        return {
            "success": False,
            "error": "Max retries exceeded for vLLM",
            "provider": self.provider_name,
        }

    async def generate_stream(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """
        Stream tokens from vLLM.
        """
        model = model_name or "meta-llama/Llama-3-8b"
        
        if messages:
            compiled_parts = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                compiled_parts.append(f"{role}: {content}")
            full_prompt = "\n".join(compiled_parts)
        else:
            full_prompt = prompt

        payload = {
            "model": model,
            "prompt": full_prompt,
            "max_tokens": 500,
            "temperature": temperature,
            "stream": True,
        }

        timeout = httpx.Timeout(timeout=120.0, connect=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{self.base_url}/v1/completions", json=payload) as response:
                    if response.status_code != 200:
                        yield f"Error: Received status code {response.status_code}"
                        return
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                choices = data.get("choices", [])
                                token = choices[0].get("text", "") if choices else ""
                                if token:
                                    yield token
                            except Exception:
                                pass
        except Exception as e:
            yield f"Error in stream generation: {str(e)}"

    async def health_check(self) -> Dict[str, Any]:
        """Check if vLLM is reachable."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                response = await client.get(f"{self.base_url}/v1/models")
                if response.status_code == 200:
                    data = response.json()
                    models = [m.get("id", "unknown") for m in data.get("data", [])]
                    return {
                        "status": "healthy",
                        "provider": self.provider_name,
                        "base_url": self.base_url,
                        "available_models": models,
                    }
                return {"status": "unhealthy", "provider": self.provider_name, "error": f"HTTP {response.status_code}"}
        except Exception as exc:
            return {"status": "unreachable", "provider": self.provider_name, "error": str(exc)}
