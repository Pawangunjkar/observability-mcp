# Changelog

## [Unreleased]

### Fixed
- **`obs_grafana_alerts` returned rule definitions, not alert states.** It now reads
  `/api/prometheus/grafana/api/v1/rules` and lists firing and pending rules first, with active instance counts.
- **Backend errors looked like results.** HTTP 4xx/5xx responses from Prometheus, Loki, Tempo, and Grafana are
  reported as errors with the backend's message (for example a PromQL parse error).
- **Large results were cut mid-JSON.** Output falls back to compact JSON and, if still too large, is clearly marked
  as truncated with a hint to narrow the query.
- **Query injection.** `obs_error_rate` escapes the service label value; the correlation, canary, and slow-trace
  tools reject service/namespace values containing quotes, backslashes, or braces.
- Creating a Grafana annotation now fails loudly on HTTP errors.

### Added
- `obs_error_rate` accepts `metric`, `service_label`, and `status_label`, so it works with Prometheus client
  libraries and OpenTelemetry metrics, not only Spring Boot/Micrometer.
- `tests/test_obs_bugfixes.py`: 8 regression tests against fake backends.
