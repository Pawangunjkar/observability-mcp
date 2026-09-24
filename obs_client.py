"""HTTP client for Prometheus, Loki, Tempo, and Grafana."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx


@dataclass
class ObsSession:
    name: str
    prometheus_url: str = ""
    loki_url: str = ""
    tempo_url: str = ""
    grafana_url: str = ""
    grafana_token: str = ""
    headers: dict[str, str] = field(default_factory=dict)


class ObsHub:
    def __init__(self) -> None:
        self.sessions: dict[str, ObsSession] = {}

    def connect(self, session: ObsSession) -> str:
        self.sessions[session.name] = session
        checks = []
        with httpx.Client(timeout=10) as client:
            for label, url in (
                ("prometheus", session.prometheus_url),
                ("loki", session.loki_url),
                ("tempo", session.tempo_url),
                ("grafana", session.grafana_url),
            ):
                if not url:
                    continue
                try:
                    response = client.get(url.rstrip("/") + ("/api/health" if label == "grafana" else "/ready"), headers=session.headers)
                    checks.append(f"{label}={response.status_code}")
                except Exception as exc:
                    checks.append(f"{label}=down ({exc.__class__.__name__})")
        return f"Session '{session.name}' saved. " + ", ".join(checks or ["no backends configured"])

    def session(self, name: str) -> ObsSession:
        if name not in self.sessions:
            raise KeyError(f"No observability session '{name}'.")
        return self.sessions[name]

    def _get(self, url: str, params: dict | None, headers: dict) -> str:
        with httpx.Client(timeout=30) as client:
            response = client.get(url, params=params, headers=headers)
        text = response.text
        try:
            return json.dumps(response.json(), indent=2)[:12000]
        except Exception:
            return text[:12000]

    def prom_query(self, session_name: str, query: str) -> str:
        session = self.session(session_name)
        return self._get(f"{session.prometheus_url.rstrip('/')}/api/v1/query", {"query": query}, session.headers)

    def loki_query(self, session_name: str, query: str, limit: int) -> str:
        session = self.session(session_name)
        return self._get(
            f"{session.loki_url.rstrip('/')}/loki/api/v1/query_range",
            {"query": query, "limit": max(1, min(limit, 500))},
            session.headers,
        )

    def tempo_trace(self, session_name: str, trace_id: str) -> str:
        session = self.session(session_name)
        return self._get(f"{session.tempo_url.rstrip('/')}/api/traces/{trace_id}", None, session.headers)

    def tempo_search(self, session_name: str, tags: str, limit: int) -> str:
        session = self.session(session_name)
        return self._get(
            f"{session.tempo_url.rstrip('/')}/api/search",
            {"tags": tags, "limit": max(1, min(limit, 50))},
            session.headers,
        )

    def grafana_get(self, session_name: str, path: str) -> str:
        session = self.session(session_name)
        headers = dict(session.headers)
        if session.grafana_token:
            headers["Authorization"] = f"Bearer {session.grafana_token}"
        return self._get(f"{session.grafana_url.rstrip('/')}{path}", None, headers)

    def grafana_annotation(self, session_name: str, text: str, tags: str) -> str:
        session = self.session(session_name)
        headers = {"Content-Type": "application/json", **session.headers}
        if session.grafana_token:
            headers["Authorization"] = f"Bearer {session.grafana_token}"
        payload = {"text": text, "tags": [tag.strip() for tag in tags.split(",") if tag.strip()]}
        with httpx.Client(timeout=20) as client:
            response = client.post(f"{session.grafana_url.rstrip('/')}/api/annotations", json=payload, headers=headers)
        return response.text[:4000]
