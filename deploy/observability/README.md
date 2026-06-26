# Jiuwenswarm Team Observability Stack

Local docker-compose deployment for OpenTelemetry observability with Langfuse backend.

## Architecture

```
Application --(OTLP gRPC)--> OTel Collector --(OTLP HTTP)--> Langfuse
           localhost:4317                        localhost:3000
```

- **Application → Collector**: gRPC (port 4317) or HTTP (port 4318), no auth required
- **Collector → Langfuse**: HTTP with Basic Auth (configured in `otel-collector-config.yaml`)

## Prerequisites

- Docker & Docker Compose

## Quick Start

```bash
cd deploy/observability

# Start all services
docker-compose up -d

# Check status
docker-compose ps

# View collector logs
docker-compose logs -f otel-collector
```

Wait for all services to become healthy (~30-60s on first start).

### Access Langfuse UI

- URL: http://localhost:3000
- Login email: `jiuwensarm@jiuwen.local`
- Password: `jiuwenswarm`
- Project keys: `pk-lf-jiuwen` / `sk-lf-jiuwen`

### Stop and Clean Up

```bash
# Stop services (keep data)
docker-compose down

# Stop and remove all data
docker-compose down -v
```

## Enable in Jiuwenswarm

编辑 `~/.jiuwenswarm/config/config.yaml`，将 `team_observability.enabled` 设为 `true`，重启即可。

```yaml
team_observability:
  enabled: true
  endpoint: http://localhost:4317
```

## Production Notes

1. **Security**: Change `NEXTAUTH_SECRET`, `SALT`, `ENCRYPTION_KEY`, and database passwords in `docker-compose.yml` before exposing outside localhost.

2. **Sampling**: If trace volume is too high, set `sample_rate < 1.0` in `team_observability` config (e.g., 0.1).

3. **Databases**: For production, use managed Postgres and ClickHouse instead of docker-compose volumes.

4. **Remove debug exporter**: In `otel-collector-config.yaml`, remove `debug` from exporters once stable:
   ```yaml
   exporters: [otlphttp/langfuse]
   ```

## Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Service orchestration: OTel Collector, Langfuse, Postgres, ClickHouse, Redis, MinIO |
| `otel-collector-config.yaml` | Collector pipeline: receivers, processors, exporters |