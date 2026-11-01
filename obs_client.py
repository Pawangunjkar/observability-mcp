"""
HTTP Client for Prometheus, Loki, Tempo, and Grafana.

Author: Pawan Gunjkar (pawangunjkar@gmail.com)

Handles all HTTP communication with observability backends:
- Prometheus PromQL instant and range queries
- Loki LogQL range queries
- Tempo trace fetch, search, and TraceQL queries
- Grafana alerts, datasources, annotations, and Alertmanager silences
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from credential_store import CredentialStore, ObsCredentials


# Constants
DEFAULT_TIMEOUT = 30
MAX_OUTPUT_SIZE = 12_000
TRACE_ID_PATTERNS = (
    re.compile(r'"(?:trace_id|traceID|traceId)":\s*"([a-fA-F0-9]{16,32})"'),
    re.compile(r'\b(?:trace_id|traceID|traceId)=([a-fA-F0-9]{16,32})\b'),
)
SPAN_ID_PATTERNS = (
    re.compile(r'"(?:span_id|spanID|spanId)":\s*"([a-fA-F0-9]{8,16})"'),
    re.compile(r'\b(?:span_id|spanID|spanId)=([a-fA-F0-9]{8,16})\b'),
)


class ObsClient:
    """
    Observability backend HTTP client.

    Manages sessions with Prometheus, Loki, Tempo, and Grafana.
    Uses CredentialStore for layered credential resolution.
    """

    def __init__(self, credential_store: Optional[CredentialStore] = None):
        self._credentials = credential_store or CredentialStore()
        self._sessions: dict[str, ObsCredentials] = {}

    @property
    def credential_store(self) -> CredentialStore:
        return self._credentials

    def connect(
        self,
        session_name: str = "default",
        prometheus_url: str = "",
        loki_url: str = "",
        tempo_url: str = "",
        grafana_url: str = "",
        grafana_token: str = "",
        timeout: int = DEFAULT_TIMEOUT,
        save_credentials: bool = True,
    ) -> str:
        """
        Connect to observability backends and create a named session.

        Returns a status message with health probe results.
        """
        if session_name in self._sessions:
            self.disconnect(session_name)

        # Try credential store if no URLs provided
        if not any([prometheus_url, loki_url, tempo_url, grafana_url]):
            creds = self._credentials.get_credentials(session_name)
            if creds:
                prometheus_url = creds.prometheus_url
                loki_url = creds.loki_url
                tempo_url = creds.tempo_url
                grafana_url = creds.grafana_url
                grafana_token = creds.grafana_token or grafana_token
                timeout = creds.timeout
            else:
                return (
                    f"❌ No credentials found for session '{session_name}'.\n\n"
                    f"Please provide backend URLs:\n"
                    f"  • prometheus_url: Prometheus URL (e.g., http://localhost:9090)\n"
                    f"  • loki_url: Loki URL (e.g., http://localhost:3100)\n"
                    f"  • tempo_url: Tempo URL (e.g., http://localhost:3200)\n"
                    f"  • grafana_url: Grafana URL (e.g., http://localhost:3000)\n"
                    f"  • grafana_token: Grafana API key (optional)\n\n"
                    f"Example: obs_connect(prometheus_url='http://localhost:9090')"
                )

        creds = ObsCredentials(
            prometheus_url=prometheus_url.rstrip("/"),
            loki_url=loki_url.rstrip("/"),
            tempo_url=tempo_url.rstrip("/"),
            grafana_url=grafana_url.rstrip("/"),
            grafana_token=grafana_token,
            timeout=timeout,
        )
        self._sessions[session_name] = creds

        if save_credentials:
            self._credentials.save_credentials(
                session_name=session_name,
                prometheus_url=creds.prometheus_url,
                loki_url=creds.loki_url,
                tempo_url=creds.tempo_url,
                grafana_url=creds.grafana_url,
                grafana_token=creds.grafana_token,
                timeout=creds.timeout,
            )

        # Health probes
        checks: list[str] = []
        with httpx.Client(timeout=10) as client:
            for label, url in (
                ("prometheus", creds.prometheus_url),
                ("loki", creds.loki_url),
                ("tempo", creds.tempo_url),
                ("grafana", creds.grafana_url),
            ):
                if not url:
                    continue
                try:
                    endpoint = url + ("/api/health" if label == "grafana" else "/ready")
                    headers = {}
                    if label == "grafana" and creds.grafana_token:
                        headers["Authorization"] = f"Bearer {creds.grafana_token}"
                    response = client.get(endpoint, headers=headers)
                    checks.append(f"{label}={response.status_code}")
                except Exception as exc:
                    checks.append(f"{label}=down ({exc.__class__.__name__})")

        status = ", ".join(checks or ["no backends configured"])
        return f"✅ Session '{session_name}' saved. {status}"

    def disconnect(self, session_name: str) -> str:
        """Remove a session from memory."""
        self._sessions.pop(session_name, None)
        return f"Disconnected '{session_name}'."

    def list_sessions(self) -> list[dict]:
        """List active sessions with masked tokens."""
        result = []
        for name, creds in self._sessions.items():
            result.append({
                "session_name": name,
                "prometheus": bool(creds.prometheus_url),
                "loki": bool(creds.loki_url),
                "tempo": bool(creds.tempo_url),
                "grafana": bool(creds.grafana_url),
            })
        return result

    def session(self, name: str) -> ObsCredentials:
        """Get session credentials by name, raising KeyError if not found."""
        if name not in self._sessions:
            raise KeyError(f"No observability session '{name}'. Call obs_connect first.")
        return self._sessions[name]

    # ---------------------------------------------------------------
    # Low-level HTTP helpers
    # ---------------------------------------------------------------

    def _get(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> str:
        """GET request, return JSON-formatted or raw text (truncated)."""
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, params=params, headers=headers or {})
        try:
            return json.dumps(response.json(), indent=2)[:MAX_OUTPUT_SIZE]
        except Exception:
            return response.text[:MAX_OUTPUT_SIZE]

    def _get_json(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> Any:
        """GET request, return parsed JSON."""
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, params=params, headers=headers or {})
            response.raise_for_status()
        return response.json()

    def _post_json(
        self,
        url: str,
        payload: dict,
        headers: dict | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> Any:
        """POST JSON, return parsed response."""
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload, headers=headers or {})
            response.raise_for_status()
        return response.json()

    def _grafana_headers(self, session: ObsCredentials) -> dict[str, str]:
        """Build Grafana API headers with bearer token."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if session.grafana_token:
            headers["Authorization"] = f"Bearer {session.grafana_token}"
        return headers

    # ---------------------------------------------------------------
    # Prometheus
    # ---------------------------------------------------------------

    def prom_query(self, session_name: str, query: str) -> str:
        """Execute an instant PromQL query."""
        session = self.session(session_name)
        return self._get(
            f"{session.prometheus_url}/api/v1/query",
            {"query": query},
            timeout=session.timeout,
        )

    def prom_query_json(self, session_name: str, query: str) -> dict:
        """Execute an instant PromQL query, return parsed JSON."""
        session = self.session(session_name)
        return self._get_json(
            f"{session.prometheus_url}/api/v1/query",
            {"query": query},
            timeout=session.timeout,
        )

    def prom_query_range(
        self,
        session_name: str,
        query: str,
        start: float,
        end: float,
        step: str = "60s",
    ) -> dict:
        """Execute a range PromQL query, return parsed JSON."""
        session = self.session(session_name)
        return self._get_json(
            f"{session.prometheus_url}/api/v1/query_range",
            {"query": query, "start": start, "end": end, "step": step},
            timeout=session.timeout,
        )

    def prom_alerts(self, session_name: str) -> dict:
        """Fetch active Prometheus alerts."""
        session = self.session(session_name)
        return self._get_json(
            f"{session.prometheus_url}/api/v1/alerts",
            timeout=session.timeout,
        )

    # ---------------------------------------------------------------
    # Loki
    # ---------------------------------------------------------------

    def loki_query(self, session_name: str, query: str, limit: int) -> str:
        """Execute a LogQL range query, return formatted text."""
        session = self.session(session_name)
        return self._get(
            f"{session.loki_url}/loki/api/v1/query_range",
            {"query": query, "limit": max(1, min(limit, 500))},
            timeout=session.timeout,
        )

    def loki_query_json(
        self,
        session_name: str,
        query: str,
        limit: int = 100,
        start: Optional[float] = None,
        end: Optional[float] = None,
    ) -> dict:
        """Execute a LogQL range query, return parsed JSON."""
        session = self.session(session_name)
        params: dict[str, Any] = {
            "query": query,
            "limit": max(1, min(limit, 500)),
        }
        if start is not None:
            params["start"] = int(start * 1_000_000_000)
        if end is not None:
            params["end"] = int(end * 1_000_000_000)
        return self._get_json(
            f"{session.loki_url}/loki/api/v1/query_range",
            params,
            timeout=session.timeout,
        )

    # ---------------------------------------------------------------
    # Tempo
    # ---------------------------------------------------------------

    def tempo_trace(self, session_name: str, trace_id: str) -> str:
        """Fetch a single trace by ID, return formatted text."""
        session = self.session(session_name)
        return self._get(
            f"{session.tempo_url}/api/traces/{trace_id}",
            timeout=session.timeout,
        )

    def tempo_trace_json(self, session_name: str, trace_id: str) -> dict:
        """Fetch a single trace by ID, return parsed JSON."""
        session = self.session(session_name)
        return self._get_json(
            f"{session.tempo_url}/api/traces/{trace_id}",
            timeout=session.timeout,
        )

    def tempo_search(self, session_name: str, tags: str, limit: int) -> str:
        """Search Tempo traces by tags, return formatted text."""
        session = self.session(session_name)
        return self._get(
            f"{session.tempo_url}/api/search",
            {"tags": tags, "limit": max(1, min(limit, 50))},
            timeout=session.timeout,
        )

    def tempo_search_json(
        self,
        session_name: str,
        query: str,
        limit: int = 20,
        min_duration: Optional[str] = None,
        max_duration: Optional[str] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> dict:
        """
        Search Tempo traces using TraceQL or tags.

        query: TraceQL expression (e.g. '{span.http.status_code >= 500}')
        """
        session = self.session(session_name)
        params: dict[str, Any] = {"q": query, "limit": max(1, min(limit, 50))}
        if min_duration:
            params["minDuration"] = min_duration
        if max_duration:
            params["maxDuration"] = max_duration
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        return self._get_json(
            f"{session.tempo_url}/api/search",
            params,
            timeout=session.timeout,
        )

    # ---------------------------------------------------------------
    # Grafana
    # ---------------------------------------------------------------

    def grafana_get(self, session_name: str, path: str) -> str:
        """GET from Grafana API, return formatted text."""
        session = self.session(session_name)
        return self._get(
            f"{session.grafana_url}{path}",
            headers=self._grafana_headers(session),
            timeout=session.timeout,
        )

    def grafana_get_json(self, session_name: str, path: str) -> Any:
        """GET from Grafana API, return parsed JSON."""
        session = self.session(session_name)
        return self._get_json(
            f"{session.grafana_url}{path}",
            headers=self._grafana_headers(session),
            timeout=session.timeout,
        )

    def grafana_annotation(
        self,
        session_name: str,
        text: str,
        tags: list[str],
        dashboard_id: Optional[int] = None,
        panel_id: Optional[int] = None,
    ) -> str:
        """Create a Grafana annotation."""
        session = self.session(session_name)
        payload: dict[str, Any] = {"text": text, "tags": tags}
        if dashboard_id:
            payload["dashboardId"] = dashboard_id
        if panel_id:
            payload["panelId"] = panel_id
        headers = self._grafana_headers(session)
        with httpx.Client(timeout=session.timeout) as client:
            response = client.post(
                f"{session.grafana_url}/api/annotations",
                json=payload,
                headers=headers,
            )
        return response.text[:4000]

    def alertmanager_create_silence(
        self,
        session_name: str,
        matchers: list[dict[str, str]],
        comment: str,
        created_by: str = "observability-mcp",
        duration_minutes: int = 30,
    ) -> dict:
        """
        Create a silence in Grafana Alertmanager.

        matchers: [{"name": "alertname", "value": "HighErrorRate", "isRegex": false}]
        """
        session = self.session(session_name)
        now = time.time()
        starts_at = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now))
        ends_at = time.strftime(
            "%Y-%m-%dT%H:%M:%S.000Z",
            time.gmtime(now + duration_minutes * 60),
        )
        payload = {
            "matchers": matchers,
            "startsAt": starts_at,
            "endsAt": ends_at,
            "createdBy": created_by,
            "comment": comment,
        }
        return self._post_json(
            f"{session.grafana_url}/api/alertmanager/grafana/api/v2/silences",
            payload,
            headers=self._grafana_headers(session),
            timeout=session.timeout,
        )

    # ---------------------------------------------------------------
    # Trace ID extraction helpers
    # ---------------------------------------------------------------

    @staticmethod
    def extract_trace_ids(text: str, max_ids: int = 10) -> list[str]:
        """Extract unique trace IDs from log text using regex patterns."""
        found: list[str] = []
        seen: set[str] = set()
        for pattern in TRACE_ID_PATTERNS:
            for match in pattern.finditer(text):
                tid = match.group(1)
                if tid not in seen:
                    seen.add(tid)
                    found.append(tid)
                    if len(found) >= max_ids:
                        return found
        return found

    @staticmethod
    def extract_span_ids(text: str, max_ids: int = 10) -> list[str]:
        """Extract unique span IDs from log text using regex patterns."""
        found: list[str] = []
        seen: set[str] = set()
        for pattern in SPAN_ID_PATTERNS:
            for match in pattern.finditer(text):
                sid = match.group(1)
                if sid not in seen:
                    seen.add(sid)
                    found.append(sid)
                    if len(found) >= max_ids:
                        return found
        return found
