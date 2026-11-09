# Deploying a Python API to Azure Container Apps (with autoscaling + monitoring)

Generic recipe. Replace the `<placeholders>`. Works for any FastAPI (or similar) app that reads its keys from env vars.

---

## Skills to review

What you need to understand (not memorize) to do this confidently. ✅ = used here, ⬜ = not used but worth knowing.

**Web APIs / HTTP** ✅
- Client vs server, request/response, URLs, ports, `localhost` vs `0.0.0.0`
- HTTP methods (GET reads, POST sends data), JSON bodies
- Status codes: 200 OK, 400 bad request, 404, 422 validation error, 429 rate limited, 500 server error, 503 unavailable
- Timeouts, retries, rate limits; HTTPS/TLS at a basic level
- Calling an API from Python (`requests`) and with tools (`/docs`, curl, Postman)

**FastAPI + Uvicorn** ✅
- `app = FastAPI()`, route decorators (`@app.get`, `@app.post`)
- Pydantic models for request validation
- `def` vs `async def` (blocking calls in `async def` freeze the server)
- Uvicorn = the ASGI server that runs the app; `module:variable` syntax; `--reload`, `--host`, `--port`
- Auto docs at `/docs` (OpenAPI/Swagger)
- ⬜ Dependencies, middleware, auth (API keys/OAuth), background tasks, multiple workers

**Python environment** ✅
- Virtual environments, `python -m pip` / `python -m uvicorn` (use the venv's copy)
- `requirements.txt` and version pinning
- Environment variables and `.env` files (`python-dotenv`); never commit keys
- File paths (`Path(__file__)`), imports, `__main__`

**Docker** ✅
- Image vs container; Dockerfile instructions (`FROM`, `WORKDIR`, `COPY`, `RUN`, `EXPOSE`, `CMD`)
- Layer caching (copy requirements before code)
- `docker build`, `docker run -p host:container`, `--env-file`, `docker ps`, `docker logs`, `docker stop`
- `.dockerignore` and why secrets must never be baked into an image
- Container registries (ACR, Docker Hub): tag, push, pull
- ⬜ Multi-stage builds, docker compose (multiple services), volumes, non-root users, image size

**Cloud basics (Azure)** ✅
- Subscription -> resource group -> resources; regions
- Azure CLI (`az login`, `az account`, `az group`, extensions); Portal navigation
- Secrets vs env vars; `secretref:`
- Cost awareness: what bills per hour vs per request; cleaning up
- ⬜ IAM/RBAC and managed identities, Key Vault, VNets/private endpoints

**Azure Container Apps** ✅
- Environment, app, revision, replica
- Ingress (external/internal), target port, FQDN
- Revisions: new one on every config change; health states; restart
- Scale rules (HTTP concurrency), min/max replicas, scale to zero, polling interval, cooldown

**Scaling and load testing** ✅
- Horizontal (more replicas) vs vertical (bigger machine) scaling
- Concurrency vs throughput vs latency
- Stateless design: in-memory state (counters, caches) is per replica
- Upstream bottlenecks (LLM rate limits) cap what scaling can do
- Writing a load test with threads; ⬜ tools like Locust, k6, hey

**Monitoring / observability** ✅
- Metrics (numbers over time) vs logs (events) vs ⬜ traces
- Azure Monitor Metrics, Log stream, Log Analytics + basic KQL (`where`, `summarize`, `bin`, `take`)
- What to watch: request count, error rate, latency, replica count, CPU/memory
- ⬜ Alerts, dashboards, Application Insights, OpenTelemetry

**LLM-specific in production** ✅
- API gateways (Azure API Management in front of Azure OpenAI)
- Token-based cost tracking, model tiering (cheap model for easy steps)
- Content filters (hard block vs soft flag), streaming quirks
- Rate limits / quotas and retry behavior of the SDK

**Kubernetes** ✅ (locally on Docker Desktop; Container Apps runs on it and hides it)
- Pod, Deployment, Service, Secret ✅; ⬜ Ingress, ConfigMap, Namespace
- `kubectl get/describe/logs/apply/top`, kube contexts, YAML manifests, labels + selectors
- Readiness probes, resource requests/limits (HPA % is relative to the CPU request)
- Horizontal Pod Autoscaler (HPA) + metrics-server = Container Apps' scale rule; KEDA (what Container Apps uses for HTTP scaling)
- CPU is a poor scaling signal for I/O-bound apps (LLM calls mostly wait on the network)
- ⬜ Helm charts; managed clusters (AKS, EKS, GKE); pushing images to a registry the cluster can pull from
- When to choose it: many services, fine-grained control, team already runs a cluster

**Experiment tracking (MLflow)** ✅
- Experiment -> run -> params (inputs) + metrics (results); `mlflow ui` at localhost:5000
- MLflow 3 UI: GenAI view = traces; **Model training** view = classic runs/metrics
- Runs are stored where the script runs (`mlflow.db` / `mlruns/`) -- start the UI from the same folder
- ⬜ Tracing LLM calls (`mlflow.openai.autolog()`), W&B as the alternative

**DevOps extras** ⬜
- Git workflow; CI/CD (GitHub Actions build -> push -> deploy on every commit)
- Infrastructure as Code (Bicep, Terraform) instead of hand-typed CLI commands
- Health checks / readiness probes, blue-green or canary deploys via revisions
- Other platforms that do the same job: AWS App Runner/ECS, Google Cloud Run

---

## 0. One-time setup

- Install **Docker Desktop** and the **Azure CLI** (`winget install Microsoft.AzureCLI`).
- Open a **new** terminal after installing (so `az` is on PATH).
- `az login` -> pick your subscription. Check with `az account show -o table`.
- `az extension add --name containerapp --upgrade`

---

## 1. Make the app a web server

Minimum FastAPI shape:

```python
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class Query(BaseModel):
    question: str

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/ask")
def ask(body: Query):          # plain `def` (not async) if your code makes blocking API calls
    return {"answer": run_my_pipeline(body.question)}
```

- Read keys with `os.getenv("MY_KEY")`. **Never hardcode keys.**
- `load_dotenv(...)` is fine locally; in the cloud the env vars come from Azure secrets (step 5).
- Notebook-only stuff (plots, evals, ingestion) stays out of the server -- the server only *connects* to existing resources.

Run locally (from your venv):

```bash
python -m uvicorn app:app --reload
```

Open http://localhost:8000/docs and test.

---

## 2. Project files

**requirements.txt** -- pin what you actually import (`pip freeze | findstr <pkg>` to get versions).

**.dockerignore** -- keep secrets and junk out of the image:

```
.env
.env.*
__pycache__/
*.pyc
.venv/
*.ipynb
.ipynb_checkpoints/
```

**Dockerfile**:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

`--host 0.0.0.0` is required inside a container.

---

## 3. Test the container locally

```bash
docker build -t <app-name> .
docker run --rm -p 8000:8000 --env-file <path-to>/.env <app-name>
```

Browse to **http://localhost:8000/docs** (not 0.0.0.0). Stop any local uvicorn first so port 8000 is free.

---

## 4. Deploy

Run **from the folder with the Dockerfile** (not `C:\Windows\system32`):

```bash
az containerapp up -n <app-name> -g <resource-group> -l eastus --source . --ingress external --target-port 8000
```

This creates the resource group, Container Apps environment, Log Analytics workspace and container registry, builds the image in the cloud and deploys it. It prints the URL (FQDN).

---

## 5. Add secrets (keys)

```bash
az containerapp secret set -n <app-name> -g <resource-group> --secrets my-key=<value> other-key=<value>

az containerapp update -n <app-name> -g <resource-group> --set-env-vars MY_KEY=secretref:my-key OTHER_KEY=secretref:other-key
```

**Both commands are needed.** `secret set` only stores the secret; `update --set-env-vars ... secretref:` hands it to the app and creates a new revision.

Check health:

```bash
az containerapp revision list -n <app-name> -g <resource-group> -o table
```

Restart if needed:

```bash
az containerapp revision restart -n <app-name> -g <resource-group> --revision <revision-name>
```

Test: `https://<fqdn>/docs`

---

## 6. Autoscaling

```bash
az containerapp update -n <app-name> -g <resource-group> --min-replicas 1 --max-replicas 5 --scale-rule-name http-rule --scale-rule-type http --scale-rule-http-concurrency 5
```

Verify:

```bash
az containerapp show -n <app-name> -g <resource-group> --query properties.template.scale
```

- Scales out when concurrent requests per replica > concurrency value.
- Azure checks every ~30 s (`pollingInterval`) and waits ~300 s before scaling down (`cooldownPeriod`).
- `--min-replicas 0` = scale to zero (cheapest, but first request after idle is slow).

---

## 7. Monitoring (portal.azure.com -> your Container App)

| Where | What it shows |
|---|---|
| Monitoring -> **Metrics** | Replica Count (Max), Requests (Sum, split by Status Code Category), Response Time (Avg). Set range to last 30 min, granularity 1 min. |
| Monitoring -> **Log stream** | live stdout/stderr |
| Monitoring -> **Logs** | KQL queries (below) |
| Application -> **Revisions and replicas** | health + replica count |

```
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(30m)
| summarize count() by bin(TimeGenerated, 1m)
```

CLI view of replicas:

```bash
az containerapp replica list -n <app-name> -g <resource-group> -o table
```

Metrics show up 1-3 min late. In-app counters (e.g. a `/metrics` endpoint) are **per replica** -- use Azure Metrics for totals.

---

## 8. Load test + MLflow tracking

`python -m pip install mlflow`

```python
import time, statistics, requests, mlflow
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

URL = "https://<fqdn>/ask"
BODY = {"question": "<test question>"}
N_REQUESTS, WORKERS = 100, 10
METRICS_URL = URL.replace("/ask", "/metrics")

def hit(_):
    start = time.perf_counter()
    try:
        status = requests.post(URL, json=BODY, timeout=180).status_code
    except requests.RequestException as e:
        status = type(e).__name__
    return status, time.perf_counter() - start

with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    results = list(pool.map(hit, range(N_REQUESTS)))

statuses = Counter(s for s, _ in results)
latencies = sorted(t for s, t in results if s == 200)
app_metrics = requests.get(METRICS_URL, timeout=30).json()

mlflow.set_experiment("<experiment-name>")
with mlflow.start_run():
    mlflow.log_params({"url": URL, "workers": WORKERS, "n_requests": N_REQUESTS})
    mlflow.log_metric("success_rate", statuses[200] / N_REQUESTS)
    if latencies:
        mlflow.log_metric("avg_latency_s", statistics.mean(latencies))
        mlflow.log_metric("p95_latency_s", latencies[int(0.95 * len(latencies)) - 1])
    for model, usd in app_metrics.get("cost_by_model", {}).items():
        mlflow.log_metric(f"cost_{model}", usd)

print(statuses)
```

View: `mlflow ui` (same folder) -> http://localhost:5000 -> **Model training** -> experiment -> run -> Overview (params) + Model metrics (charts).

- Concurrency (`max_workers`) must exceed the scale-rule concurrency or nothing scales.
- The test must run longer than ~1 min, or it ends before new replicas come up.
- Every request costs real LLM money -- estimate (cost per request x N) first. Test with `N_REQUESTS = 5`.
- Timeouts under load are often **upstream rate limits (429)**, not your app -- check the Log stream.
- Screenshot Metrics right after the test, and again ~10 min later to show scale-down.
- In-app `/metrics` is per replica: lower-than-expected cost = load spread across replicas.

---

## 9. Kubernetes (local, Docker Desktop)

Same container, run on a real Kubernetes cluster. The manifests work on AKS/EKS/GKE too (swap the image for a registry path and drop `imagePullPolicy: Never`).

**Turn on the cluster:** Docker Desktop -> Settings -> Kubernetes -> Enable -> choose **Kubeadm** (kind needs the containerd image store, and can't see your local images) -> Apply. Wait for the green indicator.

```bash
kubectl config use-context docker-desktop
kubectl get nodes                      # docker-desktop  Ready
```

**`k8s/deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: <app-name>
spec:
  replicas: 1
  selector:
    matchLabels: {app: <app-name>}
  template:
    metadata:
      labels: {app: <app-name>}       # must match the selector
    spec:
      containers:
        - name: <app-name>
          image: <app-name>            # local image from `docker build -t <app-name> .`
          imagePullPolicy: Never
          ports:
            - containerPort: 8000
          envFrom:
            - secretRef: {name: <app-name>-secrets}
          readinessProbe:
            httpGet: {path: /health, port: 8000}
          resources:
            requests: {cpu: 250m, memory: 512Mi}   # HPA % is relative to this
            limits:   {cpu: 500m, memory: 1Gi}
```

**`k8s/service.yaml`**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: <app-name>
spec:
  type: LoadBalancer                   # Docker Desktop maps this to localhost
  selector: {app: <app-name>}
  ports:
    - port: 80
      targetPort: 8000
```

**`k8s/hpa.yaml`**

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: <app-name>
spec:
  scaleTargetRef: {apiVersion: apps/v1, kind: Deployment, name: <app-name>}
  minReplicas: 1
  maxReplicas: 5
  metrics:
    - type: Resource
      resource:
        name: cpu
        target: {type: Utilization, averageUtilization: 70}
```

**Deploy:**

```bash
docker build -t <app-name> .
kubectl create secret generic <app-name>-secrets --from-env-file=<path-to>/.env   # no quotes around values in .env
kubectl apply -f k8s/
kubectl get pods,svc,hpa               # wait for 1/1 Running
```

Test at http://localhost/docs. Logs: `kubectl logs deploy/<app-name>`.

**metrics-server** (the HPA shows `<unknown>` without it):

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

PowerShell (single quotes outside, `\"` inside):

```bash
kubectl patch deployment metrics-server -n kube-system --type=json -p '[{\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/args/-\",\"value\":\"--kubelet-insecure-tls\"}]'
```

```bash
kubectl top pods
kubectl get hpa                        # cpu: 1%/70%
```

**After changing code:** `docker build -t <app-name> .` then `kubectl rollout restart deploy/<app-name>`.

**Clean up:** `kubectl delete -f k8s/` and `kubectl delete secret <app-name>-secrets`.

| Kubernetes | Azure Container Apps |
|---|---|
| Deployment | Container App revision |
| Pod | Replica |
| Service (LoadBalancer) | External ingress / FQDN |
| Secret + `envFrom` | Secrets + `secretref:` |
| HPA (CPU) + metrics-server | KEDA HTTP scale rule |
| `kubectl logs` / `top` | Log stream / Metrics |

---

## 10. Clean up

```bash
az containerapp update -n <app-name> -g <resource-group> --min-replicas 0
```

or delete everything:

```bash
az group delete -n <resource-group> --yes
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `az` not recognized | install CLI, open a new terminal / restart VS Code |
| `ModuleNotFoundError` under uvicorn | global uvicorn used -> `python -m uvicorn ...` from the venv |
| `Attribute "app" not found` | no `app = FastAPI()` in the module |
| `docker build` can't find Dockerfile | run from the folder containing it; filename exactly `Dockerfile` |
| Can't open `0.0.0.0:8000` | use `localhost:8000` |
| Permission errors from `az containerapp up` | you're in `system32`; `cd` to the project folder |
| Revision Unhealthy / Failed | missing env vars -> did step 5's `update --set-env-vars` run? Check Log stream |
| Keys load as `None` locally | wrong `load_dotenv` path |
| Load test `ReadTimeout` crashes script | wrap the request in try/except; lower concurrency |
| Replica count stays 1 | concurrency too low or test too short; verify scale rule |
| `kubectl` tries `localhost:8080` | no cluster/context: enable Kubernetes in Docker Desktop, `kubectl config get-contexts` |
| Cluster "Failed to start / pulling images" | pick Kubeadm, Reset cluster; if still failing, restart Windows |
| Docker won't start: `dockerInference ... cannot be accessed` | stale socket after a force-quit; restart Windows (never kill Docker with Stop-Process) |
| Pod stuck `0/1` | readiness probe failing -> `kubectl logs deploy/<app-name>` (often bad keys in the secret) |
| HPA `<unknown>/70%` | install + patch metrics-server, wait ~1 min |
| `kubectl patch` JSON parse error in PowerShell | single quotes outside, `\"` inside, or `--patch-file` |
| MLflow UI empty | you're in the GenAI view -> switch to **Model training**; or UI started from a different folder |
