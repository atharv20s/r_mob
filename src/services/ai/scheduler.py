"""
GPU-Aware Scheduler & Load Balancer
=====================================
Selects the best active inference node for a request based on:
1. Active requests count (primary metric: Least Active Requests)
2. Latency, throughput (tokens/sec), and VRAM usage.
"""

import logging
from typing import List, Dict, Any, Optional
from src.services.redis_service import redis_service

logger = logging.getLogger("ai.scheduler")

class GPUAwareScheduler:
    """
    Scheduler that queries Redis telemetry hashes to make optimal
    load-balancing decisions across the inference cluster.
    """

    def pick_best_node(self, provider: str, nodes: List[str]) -> Optional[str]:
        """
        Return the URL of the healthiest and least-busy node for the provider.

        Algorithm (Least Active Requests):
            1. Query telemetry/health for all nodes in 'nodes' list.
            2. Filter to only 'healthy' (or degraded if no healthy nodes exist).
            3. Sort by:
               - active_requests ASC (least busy)
               - ttft_ms ASC (lowest latency to first token)
               - tokens_per_sec DESC (highest throughput)
            4. Fall back to round-robin/first node on error/missing telemetry.
        """
        provider = provider.lower()
        if not nodes:
            return None
        if len(nodes) == 1:
            return nodes[0]

        node_telemetry_list = []
        
        # Helper to get node ID (MD5 hash prefix matching cluster.py)
        import hashlib
        def get_node_id(url: str) -> str:
            return hashlib.md5(url.encode()).hexdigest()[:8]

        for url in nodes:
            node_id = get_node_id(url)
            
            # Query health & telemetry from Redis
            health = redis_service.get_node_health(provider, node_id)
            telemetry = redis_service.get_node_telemetry(provider, node_id)
            
            status = health.get("status", "unknown") if health else "unknown"
            
            # Combine health + telemetry
            active_reqs = 0
            ttft = 99999
            tokens_per_sec = 0.0
            vram_used = 0.0

            if telemetry:
                try:
                    active_reqs = int(telemetry.get("active_requests", 0))
                    ttft = int(telemetry.get("ttft_ms", 99999))
                    tokens_per_sec = float(telemetry.get("tokens_per_sec", 0.0))
                    vram_used = float(telemetry.get("vram_used_mb", 0.0))
                except (ValueError, TypeError):
                    pass
            elif health:
                # Fallback to health latency if telemetry is absent
                try:
                    ttft = int(health.get("latency_ms", 99999))
                except (ValueError, TypeError):
                    pass

            node_telemetry_list.append({
                "url": url,
                "node_id": node_id,
                "status": status,
                "active_requests": active_reqs,
                "ttft_ms": ttft,
                "tokens_per_sec": tokens_per_sec,
                "vram_used_mb": vram_used
            })

        # 1. Filter healthy nodes
        eligible = [n for n in node_telemetry_list if n["status"] == "healthy"]
        if not eligible:
            # Fall back to degraded nodes if none are healthy
            eligible = [n for n in node_telemetry_list if n["status"] == "degraded"]
        
        if not eligible:
            # All nodes down, return first configured node as last resort
            logger.warning(f"No eligible nodes found for {provider}. Falling back to first configured node.")
            return nodes[0]

        # 2. Sort based on Least Active Requests algorithm
        # First key: active_requests (ASC)
        # Second key: ttft_ms (ASC)
        # Third key: tokens_per_sec (DESC) -> we negate it to sort ASC
        eligible.sort(key=lambda x: (
            x["active_requests"],
            x["ttft_ms"],
            -x["tokens_per_sec"]
        ))

        best = eligible[0]
        logger.info(
            f"[SCHEDULER] Selected node {best['url']} ({best['node_id']}) for {provider}. "
            f"Active reqs: {best['active_requests']}, TTFT: {best['ttft_ms']}ms, T/s: {best['tokens_per_sec']}"
        )
        return best["url"]

# Singleton instance
gpu_scheduler = GPUAwareScheduler()
