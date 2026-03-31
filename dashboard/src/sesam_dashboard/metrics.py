"""Prometheus metrics registry and metric definitions for the Sesam dashboard."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram

REGISTRY = CollectorRegistry()


def _counter(name: str, doc: str, labels: list[str]) -> Counter:
    try:
        return Counter(name, doc, labels, registry=REGISTRY)
    except ValueError:
        return REGISTRY._names_to_collectors.get(name)  # type: ignore[return-value]


def _histogram(name: str, doc: str, labels: list[str], buckets=None) -> Histogram:
    kwargs = {"registry": REGISTRY}
    if buckets is not None:
        kwargs["buckets"] = buckets
    try:
        return Histogram(name, doc, labels, **kwargs)
    except ValueError:
        return REGISTRY._names_to_collectors.get(name)  # type: ignore[return-value]


# Total HTTP requests handled by the dashboard UI + API
dashboard_requests_total: Counter = _counter(
    "dashboard_requests_total",
    "Total HTTP requests handled by the sesam-dashboard",
    ["method", "path", "status_code"],
)

# Request duration in seconds, bucketed for latency percentile queries
dashboard_request_duration_seconds: Histogram = _histogram(
    "dashboard_request_duration_seconds",
    "HTTP request duration in seconds for the sesam-dashboard",
    ["method", "path"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)
