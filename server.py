"""
Observability MCP Server.

Author: Pawan Gunjkar (pawangunjkar@gmail.com)

Query Prometheus metrics, Loki logs, Tempo traces, and Grafana alerts.
"""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

from obs_client import ObsHub, ObsSession

mcp = FastMCP(
    "observability",
    instructions=(
        "Observability MCP for Prometheus, Loki, Tempo, and Grafana. "
        "Call obs_connect first or set PROMETHEUS_URL, LOKI_URL, TEMPO_URL, GRAFANA_URL. "
        "Use obs_query_metrics, obs_query_logs, obs_get_trace, and obs_grafana_alerts. "
        "Metrics and logs are read-only. obs_annotate writes a Grafana annotation."
    ),
)

hub = ObsHub()

if any(os.environ.get(key) for key in ("PROMETHEUS_URL", "LOKI_URL", "TEMPO_URL", "GRAFANA_URL")):
    print("Auto-connecting observability session from environment...", file=sys.stderr)
    hub.connect(
        ObsSession(
            name=os.environ.get("OBS_SESSION", "default"),
            prometheus_url=os.environ.get("PROMETHEUS_URL", ""),
            loki_url=os.environ.get("LOKI_URL", ""),
            tempo_url=os.environ.get("TEMPO_URL", ""),
            grafana_url=os.environ.get("GRAFANA_URL", ""),
            grafana_token=os.environ.get("GRAFANA_TOKEN", ""),
        )
    )


@mcp.tool()
def obs_connect(
    prometheus_url: str = "",
    loki_url: str = "",
    tempo_url: str = "",
    grafana_url: str = "",
    grafana_token: str = "",
    session_name: str = "default",
) -> str:
    """Save backend URLs and probe /ready or Grafana /api/health."""
    return hub.connect(
        ObsSession(
            name=session_name,
            prometheus_url=prometheus_url,
            loki_url=loki_url,
            tempo_url=tempo_url,
            grafana_url=grafana_url,
            grafana_token=grafana_token,
        )
    )


@mcp.tool()
def obs_disconnect(session_name: str = "default") -> str:
    """Drop an observability session."""
    hub.sessions.pop(session_name, None)
    return f"Disconnected '{session_name}'."


@mcp.tool()
def obs_status() -> str:
    """List configured observability sessions."""
    if not hub.sessions:
        return "No sessions. Call obs_connect."
    lines = []
    for name, session in hub.sessions.items():
        lines.append(
            f"{name}: prom={bool(session.prometheus_url)} loki={bool(session.loki_url)} "
            f"tempo={bool(session.tempo_url)} grafana={bool(session.grafana_url)}"
        )
    return "\n".join(lines)


@mcp.tool()
def obs_query_metrics(query: str, session_name: str = "default") -> str:
    """Run an instant PromQL query, for example sum(rate(http_requests_total[5m]))."""
    try:
        return hub.prom_query(session_name, query)
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def obs_error_rate(service: str, session_name: str = "default") -> str:
    """HTTP 5xx rate for a service label over 5 minutes, if that metric exists."""
    query = f'sum(rate(http_server_requests_seconds_count{{service="{service}",status=~"5.."}}[5m]))'
    try:
        return hub.prom_query(session_name, query)
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def obs_query_logs(query: str, limit: int = 50, session_name: str = "default") -> str:
    """Run a LogQL query, for example {service=\"order-orchestrator\"} |= \"ERROR\"."""
    try:
        return hub.loki_query(session_name, query, limit)
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def obs_get_trace(trace_id: str, session_name: str = "default") -> str:
    """Fetch one Tempo trace by id."""
    try:
        return hub.tempo_trace(session_name, trace_id)
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def obs_search_traces(tags: str, limit: int = 20, session_name: str = "default") -> str:
    """Search Tempo traces. tags example: service.name=order-orchestrator."""
    try:
        return hub.tempo_search(session_name, tags, limit)
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def obs_grafana_datasources(session_name: str = "default") -> str:
    """List Grafana datasources."""
    try:
        return hub.grafana_get(session_name, "/api/datasources")
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def obs_grafana_alerts(session_name: str = "default") -> str:
    """List Grafana alert rules."""
    try:
        return hub.grafana_get(session_name, "/api/v1/provisioning/alert-rules")
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def obs_annotate(text: str, tags: str = "mcp", session_name: str = "default") -> str:
    """Create a Grafana annotation. This is the write tool for observability."""
    try:
        return hub.grafana_annotation(session_name, text, tags)
    except Exception as exc:
        return f"Error: {exc}"


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
