"""
Regression tests for Observability MCP fixes: HTTP error handling, output
truncation, Grafana alert state, and query-injection guards.

Author: Pawan Gunjkar (pawangunjkar@gmail.com)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["OBS_MCP_CACHE_DIR"] = tempfile.mkdtemp()
for var in ("PROMETHEUS_URL", "LOKI_URL", "TEMPO_URL", "GRAFANA_URL", "GRAFANA_TOKEN"):
    os.environ.pop(var, None)

import obs_client  # noqa: E402
import server  # noqa: E402
from credential_store import ObsCredentials  # noqa: E402

_REAL_CLIENT = httpx.Client


class FakeBackendTestCase(unittest.TestCase):
    def setUp(self):
        self.requests: list[httpx.Request] = []
        self.handler_fn = lambda r: httpx.Response(200, json={})

        def handler(request):
            self.requests.append(request)
            return self.handler_fn(request)

        transport = httpx.MockTransport(handler)

        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            return _REAL_CLIENT(*args, **kwargs)

        patcher = mock.patch.object(obs_client.httpx, "Client", side_effect=factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        server._obs._sessions["default"] = ObsCredentials(
            prometheus_url="http://prom.test",
            loki_url="http://loki.test",
            tempo_url="http://tempo.test",
            grafana_url="http://grafana.test",
            grafana_token="t",
            timeout=5,
        )
        self.addCleanup(server._obs._sessions.clear)


class HttpErrorTests(FakeBackendTestCase):
    def test_bad_promql_is_reported_as_error(self):
        self.handler_fn = lambda r: httpx.Response(
            400, json={"status": "error", "errorType": "bad_data", "error": "parse error at char 5"}
        )
        out = server.obs_query_metrics("sum(rate(")
        self.assertTrue(out.startswith("Error:"))
        self.assertIn("HTTP 400", out)
        self.assertIn("parse error", out)

    def test_large_result_is_marked_truncated(self):
        big = {"status": "success", "data": {"result": [{"metric": {"i": str(i)}, "value": [0, "1"]} for i in range(5000)]}}
        self.handler_fn = lambda r: httpx.Response(200, json=big)
        out = server.obs_query_metrics("up")
        self.assertIn("TRUNCATED", out)
        self.assertLessEqual(len(out), obs_client.MAX_OUTPUT_SIZE + 300)

    def test_small_result_is_valid_json(self):
        self.handler_fn = lambda r: httpx.Response(200, json={"status": "success", "data": {"result": []}})
        self.assertEqual(json.loads(server.obs_query_metrics("up"))["status"], "success")


class GrafanaAlertTests(FakeBackendTestCase):
    def test_uses_live_state_endpoint_and_sorts_firing_first(self):
        payload = {"status": "success", "data": {"groups": [{
            "name": "api", "file": "Prod",
            "rules": [
                {"name": "DiskLow", "state": "inactive", "type": "alerting", "alerts": []},
                {"name": "HighErrors", "state": "firing", "type": "alerting", "alerts": [{}, {}],
                 "annotations": {"summary": "5xx > 5%"}},
                {"name": "SlowP99", "state": "pending", "type": "alerting", "alerts": [{}]},
                {"name": "rec_rule", "type": "recording"},
            ],
        }]}}
        self.handler_fn = lambda r: httpx.Response(200, json=payload)
        out = json.loads(server.obs_grafana_alerts())
        self.assertEqual(self.requests[-1].url.path, "/api/prometheus/grafana/api/v1/rules")
        self.assertEqual([r["name"] for r in out["rules"]], ["HighErrors", "SlowP99", "DiskLow"])
        self.assertEqual(out["rules"][0]["active_instances"], 2)
        self.assertIn("firing=1", out["summary"])


class QueryEscapingTests(FakeBackendTestCase):
    def test_error_rate_escapes_service_label(self):
        self.handler_fn = lambda r: httpx.Response(200, json={"status": "success", "data": {"result": []}})
        server.obs_error_rate('api"} or vector(1) #')
        query = self.requests[-1].url.params["query"]
        self.assertIn('service="api\\"} or vector(1) #"', query)

    def test_error_rate_custom_metric_and_labels(self):
        self.handler_fn = lambda r: httpx.Response(200, json={"status": "success", "data": {"result": []}})
        server.obs_error_rate("checkout", metric="http_requests_total", status_label="code")
        query = self.requests[-1].url.params["query"]
        self.assertEqual(query, 'sum(rate(http_requests_total{service="checkout",code=~"5.."}[5m]))')
        self.assertIn("valid Prometheus name", server.obs_error_rate("x", metric="up) or (1"))

    def test_correlation_tools_reject_breakout_characters(self):
        out = server.obs_correlate_metrics_logs_and_trace('api"}|~".*')
        self.assertIn("not allowed", out)
        out = server.obs_post_deploy_canary_audit("api", 'prod"}')
        self.assertIn("not allowed", out)
        out = server.obs_get_slow_traces_and_bottlenecks("api}")
        self.assertIn("not allowed", out)
        self.assertEqual(self.requests, [])


class FormatJsonTests(unittest.TestCase):
    def test_compact_fallback_keeps_valid_json(self):
        data = {"rows": [{"k": "v" * 10} for _ in range(400)]}
        pretty_len = len(json.dumps(data, indent=2))
        out = obs_client.format_json(data, limit=pretty_len - 1)
        self.assertEqual(json.loads(out), data)


if __name__ == "__main__":
    unittest.main()
