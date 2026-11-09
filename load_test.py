import time
import statistics
import requests
import mlflow
from concurrent.futures import ThreadPoolExecutor
from collections import Counter

URL = "https://w40k-rag.victorioushill-f2ce5271.eastus.azurecontainerapps.io/ask"
QUESTION = {"question": "Can my unit shoot after it advances?"}


def hit(_):
    start = time.perf_counter()
    try:
        status = requests.post(URL, json=QUESTION, timeout=180).status_code
    except requests.RequestException as e:
        status = type(e).__name__
    return status, time.perf_counter() - start
    
N_REQUESTS = 100
WORKERS = 10
METRICS_URL = URL.replace("/ask", "/metrics")

with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    results = list(pool.map(hit, range(N_REQUESTS)))

statuses = Counter(s for s, _ in results)
latencies = sorted(t for s, t in results if s == 200)
cost = requests.get(METRICS_URL, timeout=30).json()

mlflow.set_experiment("w40k-cloud-load-test")
with mlflow.start_run():
    mlflow.log_params({"url": URL, "workers": WORKERS, "n_requests": N_REQUESTS})
    mlflow.log_metrics({
        "success_rate": statuses[200] / N_REQUESTS,
        "avg_latency_s": statistics.mean(latencies),
        "p95_latency_s": latencies[int(0.95 * len(latencies)) - 1],
        "total_cost_usd": cost["total_cost_usd"],
    })
    # loop over cost["cost_by_model"] and log each as f"cost_{model}"
    for model, usd in cost["cost_by_model"].items():
        mlflow.log_metric(f"cost_{model}", usd)

print(statuses)