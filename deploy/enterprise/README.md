# JiuwenAvatar Enterprise Deployment

This directory contains a minimal Kubernetes-style enterprise deployment
template for the distributed multi-tenant runtime.

## Runtime Shape

- `gateway`: control plane, Web/IM ingress, TriggerEngine, Mission/Report APIs.
- `agentserver`: execution plane. In production this can be created dynamically
  by RuntimeManagement; the static deployment here is for smoke tests.
- `redis`: SessionMap, Gateway leader election and trigger locks.
- `postgresql`: Avatar, Trigger, Mission and Report JSON document store.
- shared workspace: mount NFS or an equivalent RWX volume at
  `/data/jiuwenavatar-tenants`.

## Key Environment Variables

```bash
DEPLOYMENT_MODE=enterprise
AGENT_SERVER_DEPLOY_MODE=k8s
GATEWAY_SESSION_MAP_BACKEND=redis
JIUWENAVATAR_STORE_BACKEND=postgres
JIUWENAVATAR_TENANT_WORKSPACE_ROOT=/data/jiuwenavatar-tenants
REDIS_HOST=redis
DATABASE_URL=postgresql://jiuwen:jiuwen@postgresql:5432/jiuwenavatar
AGENT_SERVER_URL=ws://agentserver:28092
```

For local enterprise smoke tests without dynamic RuntimeManagement, set:

```bash
AGENT_SERVICE_ENDPOINTS={"<service_id>":"ws://agentserver:28092"}
```

If `AGENT_SERVICE_ENDPOINTS` does not contain a requested `service_id`, the
Gateway falls back to `AGENT_SERVER_URL`.
