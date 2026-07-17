"""
AI Provider Factory — Legacy Compatibility Wrapper
====================================================
This module is DEPRECATED in favor of ``src.services.ai.router.InferenceRouter``.

It is preserved as a thin wrapper so existing code that calls
``AIProviderFactory.get("mistral")`` continues to work without changes.
New code should use ``inference_router.get_adapter("provider")`` directly.
"""

from typing import Dict
from src.services.ai.base import BaseAIService


class AIProviderFactory:
    """
    Legacy factory — delegates to InferenceRouter for backward compatibility.
    """
    _providers: Dict[str, BaseAIService] = {}

    @classmethod
    def get(cls, provider: str) -> BaseAIService:
        """
        Return an AI service instance for the given provider.

        Now delegates to the InferenceRouter which supports both local
        (ollama, vllm) and cloud (mistral, openai, gemini) providers.
        """
        prov_key = provider.lower().strip()

        if prov_key not in cls._providers:
            # Try the new InferenceRouter first
            try:
                from src.services.ai.router import inference_router
                adapter = inference_router.get_adapter(prov_key)

                # If it's a cloud adapter wrapper, return the inner service
                if hasattr(adapter, '_service'):
                    cls._providers[prov_key] = adapter._service
                else:
                    # For Ollama/vLLM, wrap in a compatibility shim
                    cls._providers[prov_key] = adapter
                return cls._providers[prov_key]
            except (ImportError, ValueError):
                pass

            # Fallback to direct instantiation
            if prov_key == "mistral":
                from src.services.ai.mistral import MistralService
                cls._providers[prov_key] = MistralService()
            elif prov_key == "openai":
                from src.services.ai.openai import OpenAIService
                cls._providers[prov_key] = OpenAIService()
            elif prov_key == "gemini":
                from src.services.ai.gemini import GeminiService
                cls._providers[prov_key] = GeminiService()
            else:
                raise ValueError(f"Unsupported AI provider: {provider}")

        return cls._providers[prov_key]
