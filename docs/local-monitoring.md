# Local monitoring components

These components are opt-in and are not installed or started by the worker.
They require a running local worker and a separately configured Prometheus and
Alertmanager. Loopback listeners alone do not restrict access to other local
users: protect the collector and Alertmanager HTTP APIs and their stored data
before using them with private workflow information.

`python -m runner.metrics_http` exposes the worker's current aggregate snapshot
at `http://127.0.0.1:PORT/metrics`. Set `WORKFLOW_METRICS_HTTP_PORT` and
`WORKFLOW_METRICS_HTTP_SECRET_FILE`, along with the worker control client's
`CONTROL_SECRET_FILE`, `WORKFLOW_DATA_DIR`, and optional `WORKFLOW_CONTROL_DIR`.
Prometheus must send the metrics token as a bearer credential. The endpoint
returns 503 when it cannot obtain a current snapshot; it does not return stale
data. The endpoint does not configure a collector, retention, or API protection.

`python -m runner.desktop_alert_receiver` receives Alertmanager webhooks at
`http://127.0.0.1:PORT/alerts`. Set `WORKFLOW_ALERT_PORT` and
`WORKFLOW_ALERT_SECRET_FILE`, and configure Alertmanager's webhook
`http_config.authorization.credentials_file` with the same alert token. Use
`send_resolved: true`. The receiver accepts only the six alert names in
`observability/alerts.yml` and displays fixed messages through `notify-send`;
it does not display incoming annotations. Delivery is at least once: a retry
after a partially delivered batch can repeat a notification. A successful
`notify-send` invocation does not establish that a person saw the notification.

Each token must be a distinct 32–128 character string containing only ASCII
letters, digits, underscore, or hyphen, stored in an owner-only regular file.
Do not put token values in a unit, environment file, image, or repository.
The example user units in `observability/systemd` read paths and ports from
`%h/.config/agent-workflows/monitor.env`; the operator must also provide an
absolute `PYTHONPATH` pointing to the checkout, and the desktop service needs
the user's graphical session environment. Keep that file owner-only. The
units are examples, not an installation or a complete monitoring deployment.

To exercise the optional local integrations, first make the existing
Prometheus v3.4.2 and Alertmanager v0.28.1 container images available locally:

```sh
WORKFLOW_METRICS_HTTP_TEST=1 python -m unittest tests.test_metrics_http_integration
WORKFLOW_DESKTOP_ALERT_TEST=1 python -m unittest tests.test_desktop_alert_receiver_integration
```

These tests use temporary synthetic credentials and loopback containers. The
Alertmanager test substitutes a callback for actual desktop notifications.
