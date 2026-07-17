"""
Prometheus Metrics
==================
Gateway-level telemetry for observability.

Metrics exposed at /metrics (Prometheus scrape endpoint):
    gateway_requests_total         — Counter by provider, model, status
    gateway_latency_seconds        — Histogram by layer (inference, cache, total)
    gateway_tokens_consumed_total  — Counter by model
    gateway_cache_hits_total       — Counter
    gateway_cache_misses_total     — Counter
    gateway_rate_limited_total     — Counter
    gateway_unauthorized_total     — Counter
    gateway_errors_total           — Counter
    gateway_active_conversations   — Gauge
"""

from prometheus_client import Counter, Histogram, Gauge, make_asgi_app

# ─── Counters ────────────────────────────────────────────────────────────────

REQUEST_COUNT = Counter(
    "gateway_requests_total",
    "Total requests processed by the gateway",
    ["provider", "model", "status"],
)

TOKEN_COUNTER = Counter(
    "gateway_tokens_consumed_total",
    "Total tokens consumed across all models",
    ["model"],
)

CACHE_HITS = Counter(
    "gateway_cache_hits_total",
    "Total cache hits (responses served from Redis)",
)

CACHE_MISSES = Counter(
    "gateway_cache_misses_total",
    "Total cache misses (requests forwarded to LLM)",
)

RATE_LIMITED = Counter(
    "gateway_rate_limited_total",
    "Total requests rejected by rate limiter (429)",
)

UNAUTHORIZED = Counter(
    "gateway_unauthorized_total",
    "Total unauthorized requests (401)",
)

ERRORS = Counter(
    "gateway_errors_total",
    "Total server/LLM errors (5xx)",
)

PROMPT_INJECTION_BLOCKED = Counter(
    "gateway_prompt_injection_blocked_total",
    "Total prompts blocked by the injection filter",
)

# ─── Histograms ──────────────────────────────────────────────────────────────

LATENCY_HISTOGRAM = Histogram(
    "gateway_latency_seconds",
    "Request latency in seconds, broken down by processing layer",
    ["layer"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

# ─── Gauges ──────────────────────────────────────────────────────────────────

ACTIVE_CONVERSATIONS = Gauge(
    "gateway_active_conversations",
    "Number of active conversation threads in Redis",
)

# ─── ASGI App ────────────────────────────────────────────────────────────────

def create_metrics_app():
    """Create a Prometheus ASGI app to mount at /metrics."""
    return make_asgi_app()
