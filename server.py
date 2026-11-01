"""
Observability MCP Server — Main Entry Point.

Author: Pawan Gunjkar (pawangunjkar@gmail.com)

A comprehensive MCP server providing 15 tools across 5 categories for
querying Prometheus metrics, Loki logs, Tempo traces, and Grafana alerts
with advanced cross-signal correlation and post-deployment SRE automation.

Usage:
    uv run server.py                # Run via stdio (for Cursor/Claude Desktop)
    uv run mcp dev server.py        # Run with MCP Inspector (for testing)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from obs_client import ObsClient
from credential_store import CredentialStore

# ============================================================
# Initialize MCP Server
# ============================================================

mcp = FastMCP(
    "observability",
    instructions=(
        "Observability SRE MCP Server — Full-lifecycle post-deployment SRE & Trace Correlator "
        "for Prometheus, Loki, Tempo, and Grafana. "
        "Call obs_connect to establish a session first (or set PROMETHEUS_URL, LOKI_URL, "
        "TEMPO_URL, GRAFANA_URL, GRAFANA_TOKEN env vars). "
        "Provides 15 tools across 5 categories: connection, metrics, logs, traces, "
        "and advanced SRE correlation. "
        "PromQL, LogQL, and TraceQL queries are read-only. "
        "obs_manage_grafana_annotations_and_silences is the only mutating tool and "
        "requires confirm=true. "
        "Use obs_correlate_metrics_logs_and_trace for one-shot incident root-cause analysis "
        "and obs_post_deploy_canary_audit after k8s-mcp rollouts."
    ),
)

# Global client instance
_credential_store = CredentialStore()
_obs = ObsClient(credential_store=_credential_store)

# Auto-connect if env vars are set
_env_creds = _credential_store.get_from_env()
if _env_creds:
    print("Auto-connecting observability session from environment...", file=sys.stderr)
    _obs.connect(
        session_name=os.environ.get("OBS_SESSION", "default"),
        prometheus_url=_env_creds.prometheus_url,
        loki_url=_env_creds.loki_url,
        tempo_url=_env_creds.tempo_url,
        grafana_url=_env_creds.grafana_url,
        grafana_token=_env_creds.grafana_token,
        timeout=_env_creds.timeout,
    )


# ============================================================
# CATEGORY 1: Connection & Authentication (4 tools)
# ============================================================

@mcp.tool()
def obs_connect(
    prometheus_url: str = "",
    loki_url: str = "",
    tempo_url: str = "",
    grafana_url: str = "",
    grafana_token: str = "",
    timeout: int = 30,
    session_name: str = "default",
) -> str:
    """
    Connect to observability backends and create a named session.

    Probes /ready (Prometheus, Loki, Tempo) and /api/health (Grafana) to verify
    connectivity. Credentials are automatically cached to
    ~/.observability-mcp-cache/credentials.json for future reconnection.

    **Purpose**: Establish a session before calling any query or correlation tool.
    **When to Use**: At the start of an observability workflow, or to switch
    between staging/prod environments using different session names.
    **Behavior**: Read-Only probe (no data written to backends).
    **Side Effects**: Saves credentials to the local file cache.

    Args:
        prometheus_url: Prometheus URL (e.g., 'http://localhost:9090')
        loki_url: Loki URL (e.g., 'http://localhost:3100')
        tempo_url: Tempo URL (e.g., 'http://localhost:3200')
        grafana_url: Grafana URL (e.g., 'http://localhost:3000')
        grafana_token: Grafana API key or service account token (optional)
        timeout: Request timeout in seconds (default: 30)
        session_name: Name for this session, e.g. 'prod', 'staging' (default: 'default')
    """
    return _obs.connect(
        session_name=session_name,
        prometheus_url=prometheus_url,
        loki_url=loki_url,
        tempo_url=tempo_url,
        grafana_url=grafana_url,
        grafana_token=grafana_token,
        timeout=timeout,
    )


@mcp.tool()
def obs_disconnect(session_name: str = "default") -> str:
    """
    Disconnect an observability session.

    Removes the named session from memory. Cached credentials on disk are
    not deleted; use obs_save_credentials to manage the file cache.

    **Purpose**: Clean up a session when switching environments.
    **When to Use**: After finishing work with a specific backend set.
    **Behavior**: Read-Only (no backend communication).
    **Side Effects**: Removes session from in-memory store only.

    Args:
        session_name: Name of the session to disconnect (default: 'default')
    """
    return _obs.disconnect(session_name)


@mcp.tool()
def obs_status() -> str:
    """
    Show all active observability sessions and which backends are configured.

    **Purpose**: Quick health check to see what is connected.
    **When to Use**: Before running queries, to confirm backend availability.
    **Behavior**: Read-Only. No backend HTTP calls.
    **Side Effects**: None.
    """
    sessions = _obs.list_sessions()
    if not sessions:
        return (
            "📡 No active observability sessions.\n\n"
            "Use obs_connect to create a session:\n"
            "  obs_connect(prometheus_url='http://localhost:9090', "
            "loki_url='http://localhost:3100')"
        )
    lines = ["📡 Active Observability Sessions:\n"]
    for s in sessions:
        prom = "🟢" if s["prometheus"] else "⚪"
        loki = "🟢" if s["loki"] else "⚪"
        tempo = "🟢" if s["tempo"] else "⚪"
        graf = "🟢" if s["grafana"] else "⚪"
        lines.append(
            f"  {s['session_name']}: "
            f"Prom {prom}  Loki {loki}  Tempo {tempo}  Grafana {graf}"
        )
    return "\n".join(lines)


@mcp.tool()
def obs_save_credentials(
    session_name: str,
    prometheus_url: str = "",
    loki_url: str = "",
    tempo_url: str = "",
    grafana_url: str = "",
    grafana_token: str = "",
) -> str:
    """
    Persist observability credentials to disk for a session name.

    Saved credentials are used for auto-connect on server restart and for
    future sessions without re-entering URLs.

    **Purpose**: Cache backend URLs and tokens for reuse across sessions.
    **When to Use**: After verifying connectivity via obs_connect, to avoid
    re-entering URLs on every server restart.
    **Behavior**: Read-Only (writes to local file only, no backend calls).
    **Side Effects**: Writes to ~/.observability-mcp-cache/credentials.json.

    Args:
        session_name: Name to save these credentials under
        prometheus_url: Prometheus URL
        loki_url: Loki URL
        tempo_url: Tempo URL
        grafana_url: Grafana URL
        grafana_token: Grafana API key or service account token
    """
    _credential_store.save_credentials(
        session_name=session_name,
        prometheus_url=prometheus_url,
        loki_url=loki_url,
        tempo_url=tempo_url,
        grafana_url=grafana_url,
        grafana_token=grafana_token,
    )
    return f"✅ Credentials saved for session '{session_name}'."


# ============================================================
# CATEGORY 2: Metrics — Prometheus (2 tools)
# ============================================================

@mcp.tool()
def obs_query_metrics(query: str, session_name: str = "default") -> str:
    """
    Run an instant PromQL query against Prometheus.

    Returns the raw query result (vector, scalar, matrix, or string).
    For example: sum(rate(http_requests_total[5m]))

    **Purpose**: Execute arbitrary PromQL for metrics exploration.
    **When to Use**: To inspect any Prometheus metric — error rates, latency
    histograms, saturation counters, custom business metrics, etc.
    **Behavior**: Read-Only. Sends a GET to /api/v1/query.
    **Side Effects**: None.

    Args:
        query: PromQL expression to execute
        session_name: Observability session to use (default: 'default')
    """
    try:
        return _obs.prom_query(session_name, query)
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def obs_error_rate(service: str, session_name: str = "default") -> str:
    """
    Get the HTTP 5xx error rate for a service over the last 5 minutes.

    Uses the metric http_server_requests_seconds_count with status=~"5.."
    label filter. Returns 0 or empty if the metric does not exist.

    **Purpose**: Quick SRE check for server-side error spikes.
    **When to Use**: After a deployment, during incident triage, or as part
    of canary verification. Prefer obs_post_deploy_canary_audit for a
    full Golden Signals comparison.
    **Behavior**: Read-Only. Single PromQL instant query.
    **Side Effects**: None.

    Args:
        service: Value of the 'service' Prometheus label to filter on
        session_name: Observability session to use (default: 'default')
    """
    query = (
        f'sum(rate(http_server_requests_seconds_count'
        f'{{service="{service}",status=~"5.."}}[5m]))'
    )
    try:
        return _obs.prom_query(session_name, query)
    except Exception as exc:
        return f"Error: {exc}"


# ============================================================
# CATEGORY 3: Logs — Loki (1 tool)
# ============================================================

@mcp.tool()
def obs_query_logs(
    query: str,
    limit: int = 50,
    session_name: str = "default",
) -> str:
    """
    Run a LogQL range query against Loki.

    For example: {service="order-orchestrator"} |= "ERROR"

    Returns up to `limit` log entries (max 500) from the Loki query_range API.

    **Purpose**: Search application logs by label selectors and text filters.
    **When to Use**: During incident triage to find error messages, stack traces,
    or to extract trace_id values for Tempo correlation.
    **Behavior**: Read-Only. Sends a GET to /loki/api/v1/query_range.
    **Side Effects**: None.

    Args:
        query: LogQL expression to execute
        limit: Maximum number of log entries to return (1–500, default: 50)
        session_name: Observability session to use (default: 'default')
    """
    try:
        return _obs.loki_query(session_name, query, limit)
    except Exception as exc:
        return f"Error: {exc}"


# ============================================================
# CATEGORY 4: Traces — Tempo (2 tools)
# ============================================================

@mcp.tool()
def obs_get_trace(trace_id: str, session_name: str = "default") -> str:
    """
    Fetch a single distributed trace from Tempo by trace ID.

    Returns the full span tree with service names, operation names,
    durations, status codes, and span attributes.

    **Purpose**: Inspect one specific trace to see the full request flow.
    **When to Use**: After extracting a trace_id from Loki logs or from
    obs_correlate_metrics_logs_and_trace results.
    **Behavior**: Read-Only. Sends a GET to /api/traces/{trace_id}.
    **Side Effects**: None.

    Args:
        trace_id: Hex trace ID (16 or 32 characters)
        session_name: Observability session to use (default: 'default')
    """
    try:
        return _obs.tempo_trace(session_name, trace_id)
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def obs_search_traces(
    tags: str,
    limit: int = 20,
    session_name: str = "default",
) -> str:
    """
    Search Tempo traces by tag key=value pairs.

    Tags example: service.name=order-orchestrator http.status_code=500

    **Purpose**: Find traces matching specific attributes without a trace ID.
    **When to Use**: To discover traces for a service, HTTP path, or error
    status. For duration-based searches, use obs_get_slow_traces_and_bottlenecks.
    **Behavior**: Read-Only. Sends a GET to /api/search with tags param.
    **Side Effects**: None.

    Args:
        tags: Space-separated key=value tag pairs for trace search
        limit: Maximum number of traces to return (1–50, default: 20)
        session_name: Observability session to use (default: 'default')
    """
    try:
        return _obs.tempo_search(session_name, tags, limit)
    except Exception as exc:
        return f"Error: {exc}"


# ============================================================
# CATEGORY 5: Grafana — Alerts, Datasources (2 tools)
# ============================================================

@mcp.tool()
def obs_grafana_datasources(session_name: str = "default") -> str:
    """
    List all Grafana datasources with their types and connection details.

    **Purpose**: Discover what datasources are configured in Grafana.
    **When to Use**: To verify Prometheus/Loki/Tempo datasource availability
    or find datasource UIDs for dashboard queries.
    **Behavior**: Read-Only. Sends a GET to /api/datasources.
    **Side Effects**: None.

    Args:
        session_name: Observability session to use (default: 'default')
    """
    try:
        return _obs.grafana_get(session_name, "/api/datasources")
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def obs_grafana_alerts(session_name: str = "default") -> str:
    """
    List active Grafana alert rules with their current states.

    Returns provisioned alert rules from /api/v1/provisioning/alert-rules,
    including firing, pending, and inactive states.

    **Purpose**: Check which alerts are currently firing or pending.
    **When to Use**: During incident triage, as a starting point for
    Workflow 4 (Firing Alert Triage). For Prometheus-native alerts,
    use obs_query_metrics with the ALERTS metric instead.
    **Behavior**: Read-Only. Sends a GET to Grafana API.
    **Side Effects**: None.

    Args:
        session_name: Observability session to use (default: 'default')
    """
    try:
        return _obs.grafana_get(session_name, "/api/v1/provisioning/alert-rules")
    except Exception as exc:
        return f"Error: {exc}"


# ============================================================
# CATEGORY 6: Advanced SRE Correlation & Post-Deploy Tools (4 tools)
# ============================================================

@mcp.tool()
def obs_correlate_metrics_logs_and_trace(
    service_name: str,
    namespace: str = "",
    lookback_minutes: int = 15,
    session_name: str = "default",
) -> str:
    """
    Cross-signal incident correlator: Metrics → Logs → Traces → Root Cause.

    The flagship SRE tool. Performs a three-step correlation:
    1. Queries Prometheus for HTTP 5xx error spikes and P99 latency anomalies.
    2. Queries Loki for ERROR/Exception/Fatal logs and extracts trace_id values.
    3. Fetches the top failing distributed traces from Tempo and identifies
       the exact failing microservice, DB query, or downstream HTTP call.

    Returns a unified root-cause report with metrics summary, error logs,
    and the full span tree of the most relevant failing trace.

    **Purpose**: One-shot incident root-cause analysis across all three pillars.
    **When to Use**: When an alert fires or a user reports errors — this single
    tool replaces manually hopping between Prometheus, Loki, and Tempo.
    Prefer obs_post_deploy_canary_audit for post-deployment verification.
    **Behavior**: Read-Only. Multiple GET requests to Prometheus, Loki, and Tempo.
    **Side Effects**: None. Does not modify any backend state.

    Args:
        service_name: Kubernetes service or application name to investigate
        namespace: Kubernetes namespace filter (optional, empty = all namespaces)
        lookback_minutes: How far back to look for anomalies (default: 15 minutes)
        session_name: Observability session to use (default: 'default')
    """
    try:
        session = _obs.session(session_name)
    except KeyError as exc:
        return str(exc)

    now = time.time()
    start = now - (lookback_minutes * 60)
    report_lines: list[str] = [
        f"🔍 Cross-Signal Correlation Report: {service_name}",
        f"   Window: last {lookback_minutes} minutes"
        + (f" | Namespace: {namespace}" if namespace else ""),
        "",
    ]

    # ── Step 1: Prometheus — Error rate and P99 latency ──
    ns_filter = f',namespace="{namespace}"' if namespace else ""
    metrics_section: list[str] = ["📊 STEP 1: Prometheus Metrics"]

    error_rate_query = (
        f'sum(rate(http_server_requests_seconds_count'
        f'{{service="{service_name}"{ns_filter},status=~"5.."}}[5m])) or vector(0)'
    )
    total_rate_query = (
        f'sum(rate(http_server_requests_seconds_count'
        f'{{service="{service_name}"{ns_filter}}}[5m])) or vector(0)'
    )
    p99_query = (
        f'histogram_quantile(0.99, sum(rate(http_server_requests_seconds_bucket'
        f'{{service="{service_name}"{ns_filter}}}[5m])) by (le)) or vector(0)'
    )

    error_val = _safe_prom_scalar(_obs, session_name, error_rate_query)
    total_val = _safe_prom_scalar(_obs, session_name, total_rate_query)
    p99_val = _safe_prom_scalar(_obs, session_name, p99_query)

    error_pct = (error_val / total_val * 100) if total_val > 0 else 0.0
    metrics_section.append(f"  • Error Rate: {error_val:.4f} req/s ({error_pct:.2f}%)")
    metrics_section.append(f"  • Total RPS: {total_val:.4f} req/s")
    metrics_section.append(f"  • P99 Latency: {p99_val * 1000:.1f} ms")

    has_anomaly = error_pct > 1.0 or p99_val > 1.0
    if has_anomaly:
        metrics_section.append("  ⚠️  Anomaly detected: elevated errors or latency")
    else:
        metrics_section.append("  ✅ Metrics within normal range")

    report_lines.extend(metrics_section)
    report_lines.append("")

    # ── Step 2: Loki — Error logs and trace ID extraction ──
    log_section: list[str] = ["📝 STEP 2: Loki Error Logs"]
    ns_label = f',namespace="{namespace}"' if namespace else ""
    log_query = (
        f'{{service="{service_name}"{ns_label}}} '
        f'|~ "(?i)(error|exception|fatal|panic|traceback)"'
    )

    trace_ids: list[str] = []
    log_count = 0
    log_samples: list[str] = []

    try:
        log_result = _obs.loki_query_json(
            session_name, log_query, limit=100, start=start, end=now,
        )
        streams = log_result.get("data", {}).get("result", [])
        for stream in streams:
            values = stream.get("values", [])
            log_count += len(values)
            for _ts, line in values[:50]:
                extracted = _obs.extract_trace_ids(line, max_ids=5)
                trace_ids.extend(extracted)
                if len(log_samples) < 5:
                    truncated = line[:200] + "..." if len(line) > 200 else line
                    log_samples.append(f"    {truncated}")
    except Exception as exc:
        log_section.append(f"  ⚠️  Loki query failed: {exc}")

    trace_ids = list(dict.fromkeys(trace_ids))[:5]  # deduplicate, keep top 5
    log_section.append(f"  • Error log entries found: {log_count}")
    log_section.append(f"  • Unique trace IDs extracted: {len(trace_ids)}")
    if log_samples:
        log_section.append("  • Sample error logs:")
        log_section.extend(log_samples)
    if trace_ids:
        log_section.append(f"  • Trace IDs: {', '.join(trace_ids)}")

    report_lines.extend(log_section)
    report_lines.append("")

    # ── Step 3: Tempo — Trace analysis ──
    trace_section: list[str] = ["🔗 STEP 3: Tempo Trace Analysis"]

    if not trace_ids:
        trace_section.append("  ℹ️  No trace IDs found in logs. Searching Tempo by service name...")
        try:
            search_result = _obs.tempo_search_json(
                session_name,
                query=f'{{resource.service.name="{service_name}"}}',
                limit=5,
                start=int(start),
                end=int(now),
            )
            traces = search_result.get("traces", [])
            for t in traces[:3]:
                tid = t.get("traceID", "unknown")
                trace_ids.append(tid)
                trace_section.append(
                    f"  • Found trace: {tid} "
                    f"(root: {t.get('rootServiceName', '?')} / "
                    f"{t.get('rootTraceName', '?')}, "
                    f"duration: {t.get('durationMs', 0)}ms)"
                )
        except Exception as exc:
            trace_section.append(f"  ⚠️  Tempo search failed: {exc}")

    if trace_ids:
        for tid in trace_ids[:3]:
            try:
                trace_data = _obs.tempo_trace_json(session_name, tid)
                spans = _flatten_trace_spans(trace_data)
                if spans:
                    trace_section.append(f"\n  📍 Trace {tid} ({len(spans)} spans):")
                    # Find the root span and error spans
                    error_spans = [s for s in spans if s.get("has_error")]
                    slowest = max(spans, key=lambda s: s.get("duration_ms", 0))
                    trace_section.append(
                        f"    • Slowest span: {slowest.get('service', '?')} / "
                        f"{slowest.get('operation', '?')} "
                        f"({slowest.get('duration_ms', 0):.1f}ms)"
                    )
                    for es in error_spans[:3]:
                        trace_section.append(
                            f"    • ❌ Error span: {es.get('service', '?')} / "
                            f"{es.get('operation', '?')} "
                            f"({es.get('duration_ms', 0):.1f}ms) "
                            f"— {es.get('status_message', 'no message')}"
                        )
                    if not error_spans:
                        trace_section.append("    • No error spans in this trace")
            except Exception as exc:
                trace_section.append(f"  ⚠️  Could not fetch trace {tid}: {exc}")
    else:
        trace_section.append("  ℹ️  No traces available for analysis.")

    report_lines.extend(trace_section)
    report_lines.append("")

    # ── Summary ──
    report_lines.append("━━━ Summary ━━━")
    if has_anomaly and trace_ids:
        report_lines.append(
            "🚨 Anomaly detected with correlated traces. "
            "Review the error spans above for root cause."
        )
    elif has_anomaly:
        report_lines.append(
            "⚠️  Metrics anomaly detected but no traces found. "
            "Check if tracing is instrumented for this service."
        )
    else:
        report_lines.append("✅ No significant anomalies detected in this window.")

    return "\n".join(report_lines)


@mcp.tool()
def obs_post_deploy_canary_audit(
    service_name: str,
    namespace: str,
    baseline_minutes_ago: int = 60,
    canary_window_minutes: int = 10,
    session_name: str = "default",
) -> str:
    """
    Compare the 4 SRE Golden Signals before vs. after a deployment.

    Evaluates Latency (P95/P99), Traffic (RPS), Error Rate (%), and
    Saturation (CPU/Memory) to produce a structured PASS / WARN /
    ROLLBACK_RECOMMENDED verdict.

    Use this right after k8s-mcp performs a rollout. If the verdict is
    ROLLBACK_RECOMMENDED, trigger k8s-mcp rollback immediately.

    **Purpose**: Automated canary verification against Golden Signals.
    **When to Use**: Immediately after a Kubernetes or OpenShift deployment
    completes. This is the primary post-deploy health check tool.
    **Behavior**: Read-Only. Multiple PromQL queries comparing time windows.
    **Side Effects**: None. Returns a verdict string only.

    Args:
        service_name: Kubernetes service name being deployed
        namespace: Kubernetes namespace where the deployment lives
        baseline_minutes_ago: How many minutes before the deploy to use as baseline (default: 60)
        canary_window_minutes: Size of the post-deploy window to evaluate (default: 10)
        session_name: Observability session to use (default: 'default')
    """
    try:
        session = _obs.session(session_name)
    except KeyError as exc:
        return str(exc)

    now = time.time()
    canary_start = now - (canary_window_minutes * 60)
    baseline_end = canary_start
    baseline_start = baseline_end - (baseline_minutes_ago * 60)

    ns_filter = f',namespace="{namespace}"'
    report: list[str] = [
        f"🚀 Post-Deploy Canary Audit: {service_name} (ns: {namespace})",
        f"   Baseline: {baseline_minutes_ago}min ago → deploy | Canary: last {canary_window_minutes}min",
        "",
    ]

    signals: dict[str, dict[str, Any]] = {}
    verdict = "PASS"

    # ── 1. Latency P95 & P99 ──
    for pct, label in [("0.95", "P95"), ("0.99", "P99")]:
        query = (
            f'histogram_quantile({pct}, sum(rate('
            f'http_server_requests_seconds_bucket{{service="{service_name}"{ns_filter}}}'
            f'[5m])) by (le)) or vector(0)'
        )
        baseline_val = _safe_prom_scalar(_obs, session_name, query, ts=baseline_end)
        canary_val = _safe_prom_scalar(_obs, session_name, query, ts=now)
        pct_change = _pct_change(baseline_val, canary_val)
        status = "🟢 PASS"
        if pct_change > 50:
            status = "🔴 FAIL"
            verdict = "ROLLBACK_RECOMMENDED"
        elif pct_change > 20:
            status = "🟡 WARN"
            if verdict == "PASS":
                verdict = "WARN"
        signals[f"Latency {label}"] = {
            "baseline": f"{baseline_val * 1000:.1f}ms",
            "canary": f"{canary_val * 1000:.1f}ms",
            "change": f"{pct_change:+.1f}%",
            "status": status,
        }

    # ── 2. Traffic RPS ──
    rps_query = (
        f'sum(rate(http_server_requests_seconds_count'
        f'{{service="{service_name}"{ns_filter}}}[5m])) or vector(0)'
    )
    baseline_rps = _safe_prom_scalar(_obs, session_name, rps_query, ts=baseline_end)
    canary_rps = _safe_prom_scalar(_obs, session_name, rps_query, ts=now)
    rps_change = _pct_change(baseline_rps, canary_rps)
    rps_status = "🟢 PASS"
    if abs(rps_change) > 80:
        rps_status = "🟡 WARN"
        if verdict == "PASS":
            verdict = "WARN"
    signals["Traffic RPS"] = {
        "baseline": f"{baseline_rps:.2f}",
        "canary": f"{canary_rps:.2f}",
        "change": f"{rps_change:+.1f}%",
        "status": rps_status,
    }

    # ── 3. Error Rate % ──
    err_query = (
        f'sum(rate(http_server_requests_seconds_count'
        f'{{service="{service_name}"{ns_filter},status=~"5.."}}[5m])) or vector(0)'
    )
    baseline_err = _safe_prom_scalar(_obs, session_name, err_query, ts=baseline_end)
    canary_err = _safe_prom_scalar(_obs, session_name, err_query, ts=now)
    baseline_err_pct = (baseline_err / baseline_rps * 100) if baseline_rps > 0 else 0
    canary_err_pct = (canary_err / canary_rps * 100) if canary_rps > 0 else 0
    err_status = "🟢 PASS"
    if canary_err_pct > 5.0:
        err_status = "🔴 FAIL"
        verdict = "ROLLBACK_RECOMMENDED"
    elif canary_err_pct > 1.0 and canary_err_pct > baseline_err_pct * 2:
        err_status = "🟡 WARN"
        if verdict == "PASS":
            verdict = "WARN"
    signals["Error Rate"] = {
        "baseline": f"{baseline_err_pct:.2f}%",
        "canary": f"{canary_err_pct:.2f}%",
        "change": f"{canary_err_pct - baseline_err_pct:+.2f}pp",
        "status": err_status,
    }

    # ── 4. Saturation (CPU & Memory) ──
    cpu_query = (
        f'sum(rate(container_cpu_usage_seconds_total'
        f'{{namespace="{namespace}",container="{service_name}"}}[5m])) / '
        f'sum(kube_pod_container_resource_limits'
        f'{{namespace="{namespace}",container="{service_name}",resource="cpu"}}) '
        f'or vector(0)'
    )
    mem_query = (
        f'sum(container_memory_working_set_bytes'
        f'{{namespace="{namespace}",container="{service_name}"}}) / '
        f'sum(kube_pod_container_resource_limits'
        f'{{namespace="{namespace}",container="{service_name}",resource="memory"}}) '
        f'or vector(0)'
    )
    canary_cpu = _safe_prom_scalar(_obs, session_name, cpu_query, ts=now)
    canary_mem = _safe_prom_scalar(_obs, session_name, mem_query, ts=now)
    baseline_cpu = _safe_prom_scalar(_obs, session_name, cpu_query, ts=baseline_end)
    baseline_mem = _safe_prom_scalar(_obs, session_name, mem_query, ts=baseline_end)

    sat_status = "🟢 PASS"
    if canary_cpu > 0.9 or canary_mem > 0.9:
        sat_status = "🔴 FAIL"
        verdict = "ROLLBACK_RECOMMENDED"
    elif canary_cpu > 0.7 or canary_mem > 0.7:
        sat_status = "🟡 WARN"
        if verdict == "PASS":
            verdict = "WARN"
    signals["CPU Saturation"] = {
        "baseline": f"{baseline_cpu * 100:.1f}%",
        "canary": f"{canary_cpu * 100:.1f}%",
        "change": f"{(canary_cpu - baseline_cpu) * 100:+.1f}pp",
        "status": sat_status,
    }
    signals["Memory Saturation"] = {
        "baseline": f"{baseline_mem * 100:.1f}%",
        "canary": f"{canary_mem * 100:.1f}%",
        "change": f"{(canary_mem - baseline_mem) * 100:+.1f}pp",
        "status": sat_status,
    }

    # ── Build Report ──
    report.append("| Signal | Baseline | Canary | Change | Status |")
    report.append("|--------|----------|--------|--------|--------|")
    for sig_name, vals in signals.items():
        report.append(
            f"| {sig_name} | {vals['baseline']} | {vals['canary']} "
            f"| {vals['change']} | {vals['status']} |"
        )

    report.append("")
    verdict_icon = {"PASS": "✅", "WARN": "⚠️", "ROLLBACK_RECOMMENDED": "🚨"}.get(
        verdict, "❓"
    )
    report.append(f"━━━ VERDICT: {verdict_icon} {verdict} ━━━")

    if verdict == "ROLLBACK_RECOMMENDED":
        report.append(
            "\n🚨 Action Required: Trigger k8s-mcp rollback immediately.\n"
            "   Call k8s_rollback_deployment or equivalent."
        )
    elif verdict == "WARN":
        report.append(
            "\n⚠️  Some signals degraded. Monitor closely and consider "
            "rollback if trends worsen."
        )
    else:
        report.append("\n✅ All Golden Signals within acceptable thresholds.")

    return "\n".join(report)


@mcp.tool()
def obs_get_slow_traces_and_bottlenecks(
    service_name: str,
    min_duration_ms: int = 1000,
    limit: int = 10,
    session_name: str = "default",
) -> str:
    """
    Find slow traces and identify which span/service is the bottleneck.

    Queries Tempo using TraceQL for traces exceeding min_duration_ms, then
    analyzes the span tree to calculate which span consumed the highest
    percentage of total request time.

    **Purpose**: Pinpoint latency bottlenecks in distributed requests.
    **When to Use**: When P99 latency is high but you don't know which
    microservice or database call is the slowest. Use after
    obs_correlate_metrics_logs_and_trace if the correlation report shows
    elevated latency but you need deeper span-level analysis.
    **Behavior**: Read-Only. TraceQL search + trace fetch from Tempo.
    **Side Effects**: None.

    Args:
        service_name: Service name to filter traces for
        min_duration_ms: Minimum trace duration in milliseconds (default: 1000)
        limit: Maximum number of slow traces to analyze (1–20, default: 10)
        session_name: Observability session to use (default: 'default')
    """
    try:
        session = _obs.session(session_name)
    except KeyError as exc:
        return str(exc)

    limit = max(1, min(limit, 20))
    report: list[str] = [
        f"🐌 Slow Trace Analysis: {service_name} (min {min_duration_ms}ms)",
        "",
    ]

    # Search for slow traces
    try:
        search_result = _obs.tempo_search_json(
            session_name,
            query=f'{{resource.service.name="{service_name}"}}',
            limit=limit,
            min_duration=f"{min_duration_ms}ms",
        )
    except Exception as exc:
        return f"Error searching Tempo: {exc}"

    traces = search_result.get("traces", [])
    if not traces:
        return (
            f"No traces found for {service_name} exceeding {min_duration_ms}ms. "
            f"Try lowering min_duration_ms or check if tracing is instrumented."
        )

    report.append(f"Found {len(traces)} slow trace(s):\n")

    bottleneck_summary: dict[str, float] = {}  # service/op -> total ms contributed

    for i, trace_meta in enumerate(traces, 1):
        tid = trace_meta.get("traceID", "unknown")
        root_name = trace_meta.get("rootTraceName", "?")
        root_svc = trace_meta.get("rootServiceName", "?")
        total_ms = trace_meta.get("durationMs", 0)

        report.append(
            f"  {i}. Trace {tid}\n"
            f"     Root: {root_svc} / {root_name} | Total: {total_ms}ms"
        )

        # Fetch full trace for span analysis
        try:
            trace_data = _obs.tempo_trace_json(session_name, tid)
            spans = _flatten_trace_spans(trace_data)
            if spans and total_ms > 0:
                slowest = max(spans, key=lambda s: s.get("duration_ms", 0))
                pct = (slowest.get("duration_ms", 0) / total_ms * 100) if total_ms else 0
                svc_op = f"{slowest.get('service', '?')}/{slowest.get('operation', '?')}"
                report.append(
                    f"     🏋️ Bottleneck: {svc_op} "
                    f"({slowest.get('duration_ms', 0):.1f}ms, {pct:.0f}% of total)"
                )
                bottleneck_summary[svc_op] = (
                    bottleneck_summary.get(svc_op, 0) + slowest.get("duration_ms", 0)
                )

                # Show top 3 spans by duration
                top_spans = sorted(spans, key=lambda s: s.get("duration_ms", 0), reverse=True)[:3]
                for s in top_spans:
                    s_pct = (s.get("duration_ms", 0) / total_ms * 100) if total_ms else 0
                    err_tag = " ❌" if s.get("has_error") else ""
                    report.append(
                        f"       • {s.get('service', '?')}/{s.get('operation', '?')}: "
                        f"{s.get('duration_ms', 0):.1f}ms ({s_pct:.0f}%){err_tag}"
                    )
        except Exception as exc:
            report.append(f"     ⚠️  Could not fetch trace detail: {exc}")

        report.append("")

    # Summary: top bottleneck services
    if bottleneck_summary:
        report.append("━━━ Top Bottleneck Services ━━━")
        sorted_bottlenecks = sorted(
            bottleneck_summary.items(), key=lambda x: x[1], reverse=True
        )
        for svc_op, total_time in sorted_bottlenecks[:5]:
            report.append(f"  • {svc_op}: {total_time:.0f}ms total across traces")

    return "\n".join(report)


@mcp.tool()
def obs_manage_grafana_annotations_and_silences(
    action: str,
    text: str,
    tags: list[str],
    duration_minutes: int = 30,
    confirm: bool = False,
    session_name: str = "default",
) -> str:
    """
    Create a Grafana annotation or Alertmanager silence.

    ⚠️ CONFIRMATION-GATED MUTATING TOOL: Requires confirm=true to execute.

    Actions:
    - 'annotate': Posts a deployment marker annotation to Grafana dashboards.
      Use after k8s-mcp completes a rollout so dashboards show the deploy time.
    - 'silence': Creates a temporary Alertmanager silence matching the given tags.
      Use during planned maintenance windows to suppress noisy alerts.

    **Purpose**: Mark deployments on dashboards or silence alerts during maintenance.
    **When to Use**:
      - 'annotate': Immediately after k8s-mcp deployment succeeds (Workflow 5).
      - 'silence': Before planned maintenance or during known-good canary windows.
    **Behavior**: MUTATING. Creates a Grafana annotation or Alertmanager silence.
    **Side Effects**: Writes to Grafana API. Silences suppress alert notifications.

    Args:
        action: 'annotate' to create a Grafana annotation, or 'silence' to create
            an Alertmanager silence
        text: Annotation text or silence comment (e.g., 'Deploy v2.3.1 to prod')
        tags: List of tags for the annotation or matcher labels for the silence
            (e.g., ['deploy', 'v2.3.1'] for annotate, ['alertname=HighErrorRate'] for silence)
        duration_minutes: Silence duration in minutes (default: 30, only used for 'silence')
        confirm: Must be True to execute this mutating action (default: False)
        session_name: Observability session to use (default: 'default')
    """
    if not confirm:
        preview = (
            f"🔒 Confirmation Required\n\n"
            f"Action: {action}\n"
            f"Text: {text}\n"
            f"Tags: {', '.join(tags)}\n"
        )
        if action == "silence":
            preview += f"Duration: {duration_minutes} minutes\n"
        preview += (
            "\nThis is a mutating action. Re-call with confirm=true to execute."
        )
        return preview

    try:
        session = _obs.session(session_name)
    except KeyError as exc:
        return str(exc)

    if action == "annotate":
        try:
            result = _obs.grafana_annotation(
                session_name, text, tags,
            )
            return f"✅ Grafana annotation created.\n{result}"
        except Exception as exc:
            return f"Error creating annotation: {exc}"

    elif action == "silence":
        matchers: list[dict[str, Any]] = []
        for tag in tags:
            if "=" in tag:
                key, val = tag.split("=", 1)
                matchers.append({
                    "name": key.strip(),
                    "value": val.strip(),
                    "isRegex": False,
                })
            else:
                matchers.append({
                    "name": "alertname",
                    "value": tag.strip(),
                    "isRegex": False,
                })

        if not matchers:
            return "Error: At least one tag/matcher is required for a silence."

        try:
            result = _obs.alertmanager_create_silence(
                session_name,
                matchers=matchers,
                comment=text,
                duration_minutes=duration_minutes,
            )
            silence_id = result.get("silenceID", result.get("id", "unknown"))
            return (
                f"✅ Alertmanager silence created.\n"
                f"   Silence ID: {silence_id}\n"
                f"   Duration: {duration_minutes} minutes\n"
                f"   Comment: {text}\n"
                f"   Matchers: {json.dumps(matchers, indent=2)}"
            )
        except Exception as exc:
            return f"Error creating silence: {exc}"

    else:
        return f"Error: Unknown action '{action}'. Use 'annotate' or 'silence'."


# ============================================================
# Internal Helpers
# ============================================================

def _safe_prom_scalar(
    client: ObsClient,
    session_name: str,
    query: str,
    ts: float | None = None,
) -> float:
    """Execute a PromQL instant query and extract a single scalar value."""
    try:
        session = client.session(session_name)
        params: dict[str, Any] = {"query": query}
        if ts is not None:
            params["time"] = ts
        data = client._get_json(
            f"{session.prometheus_url}/api/v1/query",
            params,
            timeout=session.timeout,
        )
        results = data.get("data", {}).get("result", [])
        if results and len(results) > 0:
            value = results[0].get("value", [None, "0"])
            return float(value[1]) if len(value) > 1 else 0.0
    except Exception:
        pass
    return 0.0


def _pct_change(baseline: float, current: float) -> float:
    """Calculate percentage change from baseline to current."""
    if baseline == 0:
        return 0.0 if current == 0 else 100.0
    return ((current - baseline) / baseline) * 100


def _flatten_trace_spans(trace_data: dict) -> list[dict]:
    """
    Flatten an OTLP/Tempo trace response into a list of span dicts.

    Handles both Tempo's native format and the OTLP batches format.
    Returns a list of simplified span dictionaries with:
    - service, operation, duration_ms, has_error, status_message, span_id
    """
    spans: list[dict] = []

    # Try OTLP batches format (Tempo returns this)
    batches = trace_data.get("batches", [])
    if not batches:
        # Try resourceSpans format
        batches = trace_data.get("resourceSpans", [])

    for batch in batches:
        # Extract service name from resource
        resource = batch.get("resource", {})
        resource_attrs = resource.get("attributes", [])
        service_name = "unknown"
        for attr in resource_attrs:
            if attr.get("key") == "service.name":
                val = attr.get("value", {})
                service_name = val.get("stringValue", val.get("Value", "unknown"))
                break

        # Extract spans
        scope_spans_list = batch.get("scopeSpans", batch.get("instrumentationLibrarySpans", []))
        for scope_spans in scope_spans_list:
            for span in scope_spans.get("spans", []):
                duration_ns = int(span.get("endTimeUnixNano", 0)) - int(
                    span.get("startTimeUnixNano", 0)
                )
                duration_ms = duration_ns / 1_000_000

                has_error = False
                status_msg = ""
                status = span.get("status", {})
                if status.get("code") == 2 or status.get("code") == "STATUS_CODE_ERROR":
                    has_error = True
                    status_msg = status.get("message", "error")

                # Check span attributes for error indicators
                for attr in span.get("attributes", []):
                    if attr.get("key") == "otel.status_code":
                        val = attr.get("value", {})
                        if val.get("stringValue", "") == "ERROR":
                            has_error = True
                    if attr.get("key") == "error" or attr.get("key") == "exception.message":
                        val = attr.get("value", {})
                        status_msg = val.get("stringValue", status_msg)

                spans.append({
                    "service": service_name,
                    "operation": span.get("name", "unknown"),
                    "duration_ms": duration_ms,
                    "has_error": has_error,
                    "status_message": status_msg,
                    "span_id": span.get("spanId", ""),
                    "trace_id": span.get("traceId", ""),
                })

    return spans


# ============================================================
# Entry Point
# ============================================================

def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
