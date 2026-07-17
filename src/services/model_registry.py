"""
Dynamic Model Registry
======================
Manages LLM metadata dynamically in Redis instead of hardcoding.

Key: model_registry:{model_name} -> HASH
"""

import time
import logging
from typing import Dict, Any, List, Optional
from src.services.redis_service import redis_service

logger = logging.getLogger("model_registry")

class ModelRegistry:
    """
    Registry that stores LLM profiles in Redis.
    Allows the gateway to dynamically resolve model capabilities (context length, provider, etc.)
    and route requests accordingly.
    """

    def register_model(self, model_name: str, metadata: Dict[str, Any]) -> None:
        """Register or update a model in the registry."""
        if not redis_service.client:
            return
        key = f"model_registry:{model_name.lower().strip()}"
        
        # Ensure all fields are string-compatible
        serialized = {
            "model_name": model_name,
            "provider": str(metadata.get("provider", "ollama")),
            "context_length": str(metadata.get("context_length", 8192)),
            "quantization": str(metadata.get("quantization", "unknown")),
            "gpu": str(metadata.get("gpu", "optional")),
            "ram_gb": str(metadata.get("ram_gb", 0.0)),
            "status": str(metadata.get("status", "available")),
            "node": str(metadata.get("node", "")),
            "last_seen": str(int(time.time()))
        }
        
        redis_service.client.hset(key, mapping=serialized)
        logger.info(f"Registered model {model_name} in Redis registry")

    def get_model(self, model_name: str) -> Optional[Dict[str, Any]]:
        """Retrieve model metadata from the registry."""
        if not redis_service.client:
            return None
        key = f"model_registry:{model_name.lower().strip()}"
        data = redis_service.client.hgetall(key)
        if not data:
            return None
            
        # Parse fields back to their proper types
        try:
            return {
                "model_name": data.get("model_name", model_name),
                "provider": data.get("provider", "ollama"),
                "context_length": int(data.get("context_length", 8192)),
                "quantization": data.get("quantization", "unknown"),
                "gpu": data.get("gpu", "optional"),
                "ram_gb": float(data.get("ram_gb", 0.0)),
                "status": data.get("status", "available"),
                "node": data.get("node", ""),
                "last_seen": int(data.get("last_seen", 0))
            }
        except Exception:
            return data

    def list_models(self, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all registered models, optionally filtered by provider."""
        if not redis_service.client:
            return []
        
        models = []
        for key in redis_service.client.scan_iter("model_registry:*"):
            key_str = key.decode("utf-8") if isinstance(key, bytes) else key
            model_name = key_str[15:]  # Strip "model_registry:" prefix (15 chars)
            metadata = self.get_model(model_name)
            if metadata:
                if provider is None or metadata.get("provider") == provider.lower():
                    models.append(metadata)
        return models

    def sync_from_nodes(self, provider: str, node_id: str, available_models: List[str]) -> None:
        """Sync discovered models from a node's health check."""
        for model in available_models:
            # Default profiles based on model name patterns
            context_len = 8192
            quant = "unknown"
            gpu_req = "optional"
            ram = 4.0

            model_lower = model.lower()
            if "llama-3" in model_lower or "llama3" in model_lower:
                context_len = 131072
                quant = "Q4_K_M" if "8b" in model_lower else "FP16"
                ram = 8.0 if "8b" in model_lower else 48.0
            elif "gemma" in model_lower:
                context_len = 8192
                quant = "Q4_K_M"
                ram = 4.0
            elif "mistral" in model_lower:
                context_len = 32768
                quant = "Q4_K_M"
                ram = 6.0

            # Fetch existing to avoid overwriting custom metrics if any
            existing = self.get_model(model)
            nodes = []
            if existing and existing.get("node"):
                nodes = [n.strip() for n in existing.get("node").split(",") if n.strip()]
            
            if node_id not in nodes:
                nodes.append(node_id)
            
            meta = {
                "provider": provider,
                "context_length": context_len,
                "quantization": quant,
                "gpu": gpu_req,
                "ram_gb": ram,
                "status": "available",
                "node": ",".join(nodes)
            }
            self.register_model(model, meta)

    def resolve_model_name(self, model_name: str) -> str:
        """
        Resolve a short model name (e.g. 'llama3') or version-agnostic name 
        to the exact registered model name currently active in the registry (e.g. 'llama3:latest').
        """
        requested = model_name.strip().lower()
        if not requested:
            return model_name

        all_models = self.list_models()
        if not all_models:
            return model_name

        # 1. Exact match (case insensitive)
        for m in all_models:
            name = m.get("model_name", "")
            if name.lower() == requested:
                return name

        # 2. Match tag prefix (e.g. 'llama3' matches 'llama3:latest')
        for m in all_models:
            name = m.get("model_name", "")
            if name.lower().startswith(requested + ":"):
                return name

        # 3. Match substring (e.g. 'gemma' matches 'gemma3:1b')
        for m in all_models:
            name = m.get("model_name", "")
            if requested in name.lower():
                return name

        return model_name

# Singleton registry instance
model_registry = ModelRegistry()
