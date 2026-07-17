"""
Inference Router — Cluster-Aware
==================================
Provider-agnostic routing layer with multi-node cluster support.

Architecture:
    FastAPI Gateway
           │
    InferenceRouter
           │
    InferenceCluster (health-based node selection)
           │
    ┌──────┼──────────┬──────────────┐
    │      │          │              │
  Ollama  vLLM    Mistral     OpenAI/Gemini
  Pool    Pool    Cloud        Cloud
  (N nodes) (N nodes)

Usage:
    adapter = inference_router.get_adapter("ollama")
    result = await adapter.generate("Hello", "llama3.1:8b")

The router picks the healthiest node via InferenceCluster.pick_node()
before creating the adapter.  For cloud providers (no cluster),
it returns singleton instances.
"""

import logging
from typing import Dict, Any, Optional, List

from src.core.config import settings

logger = logging.getLogger("ai.router")


class InferenceRouter:
    """
    Central registry that maps provider names to adapter instances.

    For local providers (ollama, vllm): uses InferenceCluster to select
    the healthiest node and creates an adapter pointed at that node.

    For cloud providers (mistral, openai, gemini): returns a singleton
    adapter wrapper (no cluster needed).
    """

    def __init__(self):
        self._cloud_adapters: Dict[str, Any] = {}

    def _get_cluster(self):
        """Lazy import to avoid circular dependency."""
        from src.services.ai.cluster import inference_cluster
        return inference_cluster

    def get_adapter(self, provider: str) -> Any:
        """Return the adapter for the requested provider.
        
        Always routes to OllamaAdapter to run everything locally inside the machine.
        """
        from src.services.ai.ollama import OllamaAdapter
        try:
            cluster = self._get_cluster()
            node_url = cluster.pick_node("ollama") or settings.OLLAMA_BASE_URL
        except Exception:
            node_url = settings.OLLAMA_BASE_URL
            
        return OllamaAdapter(base_url=node_url)

    def list_providers(self) -> List[str]:
        """Return all supported provider names."""
        return ["ollama", "vllm", "mistral", "openai", "gemini"]

    async def health_check_all(self) -> Dict[str, Any]:
        """Run health checks on all providers via the cluster manager."""
        cluster = self._get_cluster()
        return await cluster.health_check_all()

    async def generate_with_fallback(
        self,
        prompt: str,
        model_name: str,
        messages: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.7,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Ollama-first inference with automatic cloud fallback.

        Flow:
            Request → Ollama Cluster (primary)
                ↓ (if unavailable)
            Next provider in FALLBACK_PROVIDERS
                ↓ (if unavailable)
            Next fallback...
                ↓
            {"success": False, "error": "All providers unavailable"}

        Cloud is just a fallback — not the primary path.
        """
        primary = settings.DEFAULT_PROVIDER.lower()

        # ── Try primary provider first ───────────────────────────────────
        try:
            adapter = self.get_adapter(primary)
            result = await adapter.generate(
                prompt=prompt,
                model_name=model_name,
                messages=messages,
                temperature=temperature,
                **kwargs,
            )
            if result.get("success"):
                return result
            logger.warning(
                "Primary provider '%s' returned error: %s",
                primary, result.get("error", "unknown"),
            )
        except Exception as exc:
            logger.warning("Primary provider '%s' failed: %s", primary, exc)

        # ── Fallback chain ───────────────────────────────────────────────
        fallback_chain = getattr(settings, "FALLBACK_PROVIDERS", "")
        for fallback in fallback_chain.split(","):
            fallback = fallback.strip().lower()
            if not fallback or fallback == primary:
                continue

            try:
                adapter = self.get_adapter(fallback)
                result = await adapter.generate(
                    prompt=prompt,
                    model_name=model_name,
                    messages=messages,
                    temperature=temperature,
                    **kwargs,
                )
                if result.get("success"):
                    result["fallback_from"] = primary
                    result["fallback_provider"] = fallback
                    logger.info(
                        "Failover: %s -> %s (success)", primary, fallback,
                    )
                    return result
            except Exception as exc:
                logger.warning("Failover provider '%s' failed: %s", fallback, exc)
                continue

        return {
            "success": False,
            "error": f"All providers unavailable (tried: {primary}, {fallback_chain})",
            "provider": primary,
        }


# ─── Module-level singleton ─────────────────────────────────────────────────
inference_router = InferenceRouter()
