from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class BaseAIService(ABC):
    @abstractmethod
    async def generate_text(self, prompt: str, model: Optional[str] = None) -> Dict[str, Any]:
        """Asynchronously generate text responses for a given prompt."""
        pass
