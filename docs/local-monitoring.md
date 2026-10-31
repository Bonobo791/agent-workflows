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
`observability/local/monitor.env.example` lists the required paths and ports;
replace its placeholders in a private copy, without putting token values in it.

## Collector and router configuration

`observability/local/prometheus.yml` and `alertmanager.yml` provide a matching
loopback-only configuration: metrics on 19190, Alertmanager on 19093, and the
receiver on 19192. Set the two endpoint ports in `monitor.env` accordingly.
These examples have not been installed or enabled.

Prepare separate owner-only configuration and persistent data directories for
Prometheus and Alertmanager. Copy the corresponding example into each config
directory, and copy `observability/alerts.yml` into the Prometheus config
directory. The following files must also be present in the respective config
directories, accessible to the user running the containers:

| Directory | File | Contents |
| --- | --- | --- |
| Prometheus | `scrape-token` | Same token as `WORKFLOW_METRICS_HTTP_SECRET_FILE` |
| Prometheus | `alertmanager-password` | The Alertmanager API password |
| Alertmanager | `alert-token` | Same token as `WORKFLOW_ALERT_SECRET_FILE` |
| Each | `web.yml` | Its own `basic_auth_users` mapping with bcrypt password hashes |

The Alertmanager `web.yml` must include the username `workflow` and a bcrypt
hash of the password in `alertmanager-password`. Give Prometheus a separate
API username and password. Generate strong, distinct secrets privately; never
copy the synthetic credentials from tests. Use mode 0600 for the files and mode
0700 for their directories. See the official [Prometheus web authentication
configuration](https://prometheus.io/docs/prometheus/latest/configuration/https/)
and [Alertmanager web authentication](https://prometheus.io/docs/alerting/latest/https/).

Run each collector with its own config directory mounted read-only at `/config`
and its own writable persistent data directory. Set `--web.config.file` to
`/config/web.yml` on **both** services. Bind the Prometheus API to
`127.0.0.1:19090`, the Alertmanager API to `127.0.0.1:19093`, and disable the
Alertmanager cluster listener unless explicitly needed. Configure bounded
retention, protect data directories and backups, and verify restart behavior.
The configuration uses HTTP on loopback: credentials and telemetry are not
encrypted against a privileged local observer. Do not expose these ports to a
network or treat loopback as an authorization boundary.

The optional `agent-workflows-prometheus.service` and
`agent-workflows-alertmanager.service` user units provide these settings for a
rootless Podman installation. They use immutable image digests, never pull an
image at startup, and expect the config directories at
`~/.config/agent-workflows/{prometheus,alertmanager}` and the persistent data
directories at `~/.local/share/agent-workflows-monitor/{prometheus,alertmanager}`.
Create those directories privately before starting the units. Prometheus is
configured for 15 days and a 2 GB block-retention target; its write-ahead log
and other files can require additional space. Alertmanager retains its data
for 120 hours. The container names are fixed; if a previous container remains,
inspect it rather than removing it blindly. These units are not installed or
started by this repository.

The optional `agent-workflows-grafana.service` listens on `127.0.0.1:13000`.
It expects private directories `~/.config/agent-workflows/grafana` and
`~/.local/share/agent-workflows-monitor/grafana`. In the configuration
directory, create owner-only `admin-password` and `secret-key` files containing
distinct strong values. Preserve the secret key with protected backups: changing
it makes stored encrypted datasource credentials unreadable. The initial
administrator is `workflow`; the password file is used when the database is
first initialized, not to rotate an existing account.

Copy `observability/dashboard.json` into `grafana/dashboards/` and copy
`observability/local/grafana/dashboards.yml` into
`grafana/provisioning/dashboards/`. Create a private
`grafana/provisioning/datasources/workflow.yml` from
`observability/local/grafana/datasource.yml.example`, replacing its placeholder
with the Prometheus API password and matching the API username. Use a password
without `$` to avoid Grafana provisioning's environment substitution. The
datasource file contains a secret: use mode 0600 and never commit it. Protect
the Grafana database and its backups. The service disables anonymous access,
signup, automatic plugin installation and plugin administration. It does not
activate Grafana alerts. Confirm the API refuses unauthenticated access before
using it, and configure HTTPS or another protected transport if access leaves
the local host.

The combined opt-in integration test uses the example configurations with
shortened timing, isolated temporary storage, and synthetic credentials. It
checks that both APIs reject unauthenticated requests, and that a worker outage
and recovery produce firing and resolved alerts through the full path. It also
restarts both containers with temporary persistent directories and verifies
Prometheus retains an earlier snapshot. It does not verify Alertmanager's
deduplication history, a real desktop notification, or production alert wait
times.
With `WORKFLOW_GRAFANA_TEST=1`, it also starts the pinned Grafana service,
provisions the dashboard and authenticated datasource with synthetic secrets,
queries every panel expression, and checks the dashboard after restart. This
includes API-level validation. The additional browser test uses the pinned
local OpenFilm renderer image to sign in, verify a synthetic queued-job series,
and check that the evidence explanation is not clipped on a mobile viewport.
It captures desktop and mobile screenshots in temporary storage; it is not a
complete accessibility review.

To exercise the optional local integrations, make the Prometheus v3.4.2 and
Alertmanager v0.28.1 images available for the first two tests, and the pinned
Prometheus v3.15.1, Alertmanager v0.34.2 and Grafana v13.3.0 images from the
unit definitions available for the combined tests:

```sh
WORKFLOW_METRICS_HTTP_TEST=1 python -m unittest tests.test_metrics_http_integration
WORKFLOW_DESKTOP_ALERT_TEST=1 python -m unittest tests.test_desktop_alert_receiver_integration
WORKFLOW_LOCAL_MONITORING_TEST=1 python -m unittest tests.test_local_monitoring_integration
WORKFLOW_LOCAL_MONITORING_TEST=1 WORKFLOW_MONITOR_SYSTEMD_TEST=1 python -m unittest tests.test_local_monitoring_integration
WORKFLOW_LOCAL_MONITORING_TEST=1 WORKFLOW_MONITOR_SYSTEMD_TEST=1 WORKFLOW_GRAFANA_TEST=1 python -m unittest tests.test_local_monitoring_integration
WORKFLOW_LOCAL_MONITORING_TEST=1 WORKFLOW_MONITOR_SYSTEMD_TEST=1 WORKFLOW_GRAFANA_TEST=1 WORKFLOW_GRAFANA_BROWSER_TEST=1 python -m unittest tests.test_local_monitoring_integration
```

These tests use temporary synthetic credentials and loopback containers. The
Alertmanager test substitutes a callback for actual desktop notifications.
