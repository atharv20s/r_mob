from typing import Dict
from src.services.ai.base import BaseAIService
from src.services.ai.mistral import MistralService
from src.services.ai.openai import OpenAIService
from src.services.ai.gemini import GeminiService

class AIProviderFactory:
    _providers: Dict[str, BaseAIService] = {}

    @classmethod
    def get(cls, provider: str) -> BaseAIService:
        prov_key = provider.lower().strip()
        
        # Lazy initialization to avoid instantiating all clients at startup if not used
        if prov_key not in cls._providers:
            if prov_key == "mistral":
                cls._providers[prov_key] = MistralService()
            elif prov_key == "openai":
                cls._providers[prov_key] = OpenAIService()
            elif prov_key == "gemini":
                cls._providers[prov_key] = GeminiService()
            else:
                raise ValueError(f"Unsupported AI provider: {provider}")
                
        return cls._providers[prov_key]
