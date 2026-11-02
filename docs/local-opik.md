# Local Opik for audit delivery

This guide is for a local evaluation of the audit exporter. Opik's Docker
Compose deployment is intended for local development and testing, not
production, and [self-hosted Opik does not include user management](https://www.comet.com/docs/opik/self-host/overview).
Do not send private audit history to it until the destination's access control,
transport, retention, backups, and ingestion behavior have been reviewed for
the data you intend to send. Loopback binding does not prevent access by other
local users.

## Start a local Opik server

The local loopback, ClickHouse, Podman, and frontend-resolver changes that made
this stack run are saved as
`observability/local/opik-2.2.120-local.patch`. They are not committed upstream
and exist only as this patch. Apply the patch to the clean `2.2.120` checkout
created below before starting the stack.

The upstream local deployment requires Docker Engine and Docker Compose. This
example pins [Opik 2.2.120](https://github.com/comet-ml/opik/releases/tag/2.2.120);
review the [self-hosted changelog](https://www.comet.com/docs/opik/self-host/changelog)
and update both version values together when upgrading.

```sh
OPIK_RELEASE=2.2.120
git clone --depth 1 --branch "$OPIK_RELEASE" https://github.com/comet-ml/opik.git
cd opik
git apply /absolute/path/to/agent-workflows/observability/local/opik-2.2.120-local.patch
OPIK_VERSION="$OPIK_RELEASE" ./opik.sh
./opik.sh --verify
```

The UI is available at `http://127.0.0.1:5173`. Upstream Compose publishes host
ports on all interfaces by default. The patch restricts the frontend, backend,
MySQL, Redis, MinIO, ClickHouse, Zookeeper, and the Python backend to loopback.
Jaeger and the OTEL collector (`opik-otel` profile) are not yet restricted;
verify every published port before using that profile. Upstream documents
local data under `~/opik`; protect and back up that directory. Do not use
`./opik.sh --clean` or `docker compose down --volumes` unless you intend to
delete Opik data. Use `./opik.sh --stop` to stop the stack while retaining its
data.

The official [local deployment guide](https://www.comet.com/docs/opik/self-host/local_deployment/)
describes this Compose setup as not production-ready. For production, Opik
recommends its Kubernetes Helm deployment. This repository does not provide
or validate that deployment.

## Send a bounded audit page

This repository's `runner.audit_delivery` command sends one bounded page per
selected table to the explicit OTLP/HTTP trace endpoint. The Opik endpoint is
`/api/v1/private/otel/v1/traces`. The existing
`agent-workflows-audit-delivery.service` targets the separate local OpenTelemetry
Collector on port 14318; it does not target Opik. No Opik server, exporter, or
delivery timer is started by this repository.

Install `requirements-observability.txt` for the Python interpreter that will
run delivery. The source database's parent, cursor database's parent, and key
directory must already exist and be owner-only. The source database and key
must be owner-only files; the key must be at least 32 characters and stay
stable for the desired deduplication period. The cursor database is created
privately if absent. Example one-page command:

```sh
python -m runner.audit_delivery \
  --source /absolute/private/worker/state.db \
  --cursor /absolute/private/opik-cursor/delivery.db \
  --key-file /absolute/private/audit-correlation-key \
  --endpoint http://127.0.0.1:5173/api/v1/private/otel/v1/traces \
  --all-present
```

Each invocation processes at most one bounded page per selected table; repeat
it to continue. A failed or ambiguous response may be retried and can duplicate
delivery. The projection omits raw run IDs,
inputs, outputs, and artifact references, but the remaining audit metadata can
still be sensitive. Keep the source, cursor, key, server data, and backups
private.

The repository has not verified authentication on the local self-hosted export
endpoint, and upstream self-hosted Opik has no user management. Treat the
endpoint as unauthenticated until you verify otherwise. Keep it on loopback and
use it only where other local users cannot access the service or its data. If
using a reverse proxy or another authenticated endpoint, configure TLS and
access control there and provide its bearer credential in an owner-only file
with `--token-file`; verify the complete route before sending data. The repository's exporter
rejects non-loopback HTTP and disables redirects, proxy discovery, and ambient
client certificates.

Before scheduling delivery, verify with synthetic data that the selected Opik
release accepts the serialized OTLP traces, rejects unauthorized requests,
retains data for the required period, and survives the restart and backup
behavior you require. These checks have not been performed against an Opik
server. The repository's fixture tests do not establish Opik compatibility,
access control, retention, or durable ingestion. There is no Opik-specific
systemd unit in this repository.
