from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, AsyncGenerator, List

class BaseInferenceAdapter(ABC):
    """
    Contract for all inference adapters in the AI Inference Platform.
    """
    provider_name: str

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.7,
        **kwargs,
    ) -> Dict[str, Any]:
        """Synchronous (batch) generation. Returns full response payload."""
        pass

    @abstractmethod
    async def generate_stream(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """Streaming generation. Yields token strings one at a time."""
        pass

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """Return health status + available models."""
        pass

    async def benchmark(self, model_name: str) -> Dict[str, Any]:
        """
        Run a micro-generation to measure TTFT (Time To First Token) and throughput.
        Uses a standard prompt "Say hello".
        """
        import time
        start_time = time.time()
        ttft = None
        token_count = 0
        
        try:
            generator = self.generate_stream(
                prompt="Say hello",
                model_name=model_name,
                temperature=0.0
            )
            async for token in generator:
                if ttft is None:
                    ttft = (time.time() - start_time) * 1000
                if token:
                    token_count += 1
            
            total_duration = time.time() - start_time
            tokens_per_sec = token_count / total_duration if total_duration > 0 else 0
            
            return {
                "success": True,
                "ttft_ms": int(ttft) if ttft is not None else 0,
                "total_ms": int(total_duration * 1000),
                "tokens_per_sec": round(tokens_per_sec, 2),
                "tokens_generated": token_count,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "ttft_ms": 0,
                "total_ms": 0,
                "tokens_per_sec": 0.0,
            }


class BaseAIService(BaseInferenceAdapter):
    """Legacy compatibility class for older service references."""
    
    async def generate_text(
        self,
        prompt: str,
        model: Optional[str] = None,
        messages: Optional[list] = None
    ) -> Dict[str, Any]:
        """Legacy compatibility wrapper delegating to generate()."""
        return await self.generate(prompt=prompt, model_name=model, messages=messages)

    async def generate_stream(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        # Provide a default stream implementation that is not implemented
        # (will be overridden by subclasses)
        raise NotImplementedError("Streaming not supported on this legacy service")
