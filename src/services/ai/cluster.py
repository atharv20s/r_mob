"""
AI Inference Cluster — Health-Based Node Routing & Telemetry
=============================================================
Manages a pool of inference nodes per provider with periodic health checks,
telemetry capture (VRAM/active models), and load-balanced routing.

The health check loop runs as a background asyncio task started in main.py.
"""

import asyncio
import hashlib
import logging
import time
from typing import Dict, List, Optional, Any

import httpx

from src.core.config import settings
from src.services.redis_service import redis_service
from src.services.model_registry import model_registry
from src.services.ai.scheduler import gpu_scheduler

logger = logging.getLogger("ai.cluster")


class InferenceCluster:
    """
    Manages multi-node inference pools with health-based routing.
    """

    def __init__(self):
        self._node_pools: Dict[str, List[str]] = {}
        self._parse_node_config()

    def _parse_node_config(self) -> None:
        """Parse comma-separated node URLs from settings."""
        # Ollama nodes
        ollama_raw = getattr(settings, "OLLAMA_NODES", settings.OLLAMA_BASE_URL)
        self._node_pools["ollama"] = [
            url.strip().rstrip("/") for url in ollama_raw.split(",") if url.strip()
        ]

        # vLLM nodes
        vllm_raw = getattr(settings, "VLLM_NODES", settings.VLLM_BASE_URL)
        self._node_pools["vllm"] = [
            url.strip().rstrip("/") for url in vllm_raw.split(",") if url.strip()
        ]

        for provider, nodes in self._node_pools.items():
            if nodes:
                logger.info(
                    "Cluster config: %s -> %d node(s): %s",
                    provider, len(nodes), nodes,
                )

    def get_nodes(self, provider: str) -> List[str]:
        """Return all configured node URLs for a provider."""
        return self._node_pools.get(provider.lower(), [])

    @staticmethod
    def _node_id(url: str) -> str:
        """Generate a short stable ID from a node URL."""
        return hashlib.md5(url.encode()).hexdigest()[:8]

    def pick_node(self, provider: str) -> Optional[str]:
        """
        Return the URL of the healthiest and least-busy node for the provider.
        Delegates selection to GPUAwareScheduler.
        """
        provider = provider.lower()
        nodes = self.get_nodes(provider)

        if not nodes:
            return None
        if len(nodes) == 1:
            return nodes[0]

        # Delegate to GPU-Aware Scheduler (Least Active Requests)
        return gpu_scheduler.pick_best_node(provider, nodes)

    async def health_check_all(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Run health checks on all configured nodes across all providers.
        Updates Redis health registry and telemetry tables.
        """
        results: Dict[str, List[Dict[str, Any]]] = {}

        for provider, nodes in self._node_pools.items():
            provider_results = []
            for url in nodes:
                health = await self._check_node(provider, url)
                provider_results.append(health)
            results[provider] = provider_results

        return results

    async def _check_node(
        self, provider: str, url: str
    ) -> Dict[str, Any]:
        """
        Health-check a single inference node, collect VRAM telemetry,
        run a benchmark, and update Redis and ModelRegistry.
        """
        node_id = self._node_id(url)
        provider = provider.lower()

        try:
            start = time.time()
            timeout = httpx.Timeout(timeout=10.0, connect=5.0)

            if provider == "ollama":
                from src.services.ai.ollama import OllamaAdapter
                adapter = OllamaAdapter(base_url=url)
                
                # 1. Direct Ping health check
                health = await adapter.health_check()
                
                if health.get("status") == "healthy":
                    models = health.get("available_models", [])
                    
                    # Pick model to benchmark (default or first available)
                    bench_model = settings.DEFAULT_MODEL
                    if bench_model not in models and models:
                        bench_model = models[0]
                    
                    # 2. Run micro-generation benchmark
                    bench_res = await adapter.benchmark(bench_model)
                    
                    # 3. Query node VRAM and active models telemetry via /api/ps
                    vram_used = 0
                    active_requests = 0
                    try:
                        async with httpx.AsyncClient(timeout=timeout) as client:
                            ps_resp = await client.get(f"{url}/api/ps")
                            if ps_resp.status_code == 200:
                                ps_data = ps_resp.json()
                                running_models = ps_data.get("models", [])
                                vram_used = sum(m.get("size_vram", 0) for m in running_models)
                                active_requests = len(running_models)
                    except Exception:
                        pass
                    
                    latency_ms = int((time.time() - start) * 1000)
                    
                    health_data = {
                        "status": "healthy",
                        "latency_ms": latency_ms,
                        "last_check": int(time.time()),
                        "models": ",".join(models),
                        "url": url,
                    }
                    
                    telemetry_data = {
                        "active_requests": active_requests,
                        "vram_used_bytes": vram_used,
                        "vram_used_mb": round(vram_used / (1024 * 1024), 2),
                        "latency_ms": latency_ms,
                        "ttft_ms": bench_res.get("ttft_ms", 99999),
                        "tokens_per_sec": bench_res.get("tokens_per_sec", 0.0),
                        "status": "healthy",
                    }
                    
                    # Sync discovered models into the ModelRegistry
                    model_registry.sync_from_nodes(provider, node_id, models)
                    
                else:
                    health_data = {
                        "status": "degraded",
                        "latency_ms": 0,
                        "last_check": int(time.time()),
                        "models": "",
                        "url": url,
                        "error": health.get("error", "unhealthy"),
                    }
                    telemetry_data = {
                        "active_requests": 0,
                        "vram_used_bytes": 0,
                        "vram_used_mb": 0.0,
                        "latency_ms": 99999,
                        "ttft_ms": 99999,
                        "tokens_per_sec": 0.0,
                        "status": "degraded",
                    }
                    
            elif provider == "vllm":
                from src.services.ai.vllm import VLLMAdapter
                adapter = VLLMAdapter(base_url=url)
                health = await adapter.health_check()
                
                if health.get("status") == "healthy":
                    models = health.get("available_models", [])
                    bench_model = models[0] if models else "meta-llama/Llama-3-8b"
                    
                    bench_res = await adapter.benchmark(bench_model)
                    latency_ms = int((time.time() - start) * 1000)
                    
                    health_data = {
                        "status": "healthy",
                        "latency_ms": latency_ms,
                        "last_check": int(time.time()),
                        "models": ",".join(models),
                        "url": url,
                    }
                    telemetry_data = {
                        "active_requests": 0,  # vLLM manages queue internally
                        "vram_used_bytes": 0,
                        "vram_used_mb": 0.0,
                        "latency_ms": latency_ms,
                        "ttft_ms": bench_res.get("ttft_ms", 99999),
                        "tokens_per_sec": bench_res.get("tokens_per_sec", 0.0),
                        "status": "healthy",
                    }
                    model_registry.sync_from_nodes(provider, node_id, models)
                else:
                    health_data = {
                        "status": "degraded",
                        "latency_ms": 0,
                        "last_check": int(time.time()),
                        "models": "",
                        "url": url,
                        "error": health.get("error", "unhealthy"),
                    }
                    telemetry_data = {
                        "active_requests": 0,
                        "vram_used_bytes": 0,
                        "vram_used_mb": 0.0,
                        "latency_ms": 99999,
                        "ttft_ms": 99999,
                        "tokens_per_sec": 0.0,
                        "status": "degraded",
                    }
            else:
                health_data = {
                    "status": "unknown",
                    "latency_ms": 0,
                    "last_check": int(time.time()),
                    "models": "",
                    "url": url,
                }
                telemetry_data = {
                    "active_requests": 0,
                    "status": "unknown",
                }

        except Exception as exc:
            health_data = {
                "status": "down",
                "latency_ms": 0,
                "last_check": int(time.time()),
                "models": "",
                "url": url,
                "error": str(exc)[:100],
            }
            telemetry_data = {
                "active_requests": 0,
                "vram_used_bytes": 0,
                "vram_used_mb": 0.0,
                "latency_ms": 99999,
                "ttft_ms": 99999,
                "tokens_per_sec": 0.0,
                "status": "down",
            }

        # Update Redis health and telemetry registries
        redis_service.update_node_health(provider, node_id, health_data)
        redis_service.update_node_telemetry(provider, node_id, telemetry_data)

        logger.info(
            "[HEALTH] %s/%s -> %s  latency=%dms  ttft=%dms  t/s=%.1f  vram=%sMB  models=%s",
            provider, node_id, health_data["status"],
            telemetry_data.get("latency_ms", 0),
            telemetry_data.get("ttft_ms", 0),
            telemetry_data.get("tokens_per_sec", 0.0),
            telemetry_data.get("vram_used_mb", 0.0),
            health_data.get("models", ""),
        )

        return health_data


async def health_check_loop() -> None:
    """
    Background task: check all inference nodes periodically.
    """
    interval = settings.HEALTH_CHECK_INTERVAL
    cluster = inference_cluster

    logger.info(
        "[HEALTH] Health check loop started (interval=%ds)", interval
    )

    while True:
        try:
            await cluster.health_check_all()
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("[HEALTH] Health check loop stopped.")
            raise
        except Exception as exc:
            logger.error("[HEALTH] Error in health check loop: %s", exc)
            await asyncio.sleep(5)


# ─── Module-level singleton ─────────────────────────────────────────────────
inference_cluster = InferenceCluster()
