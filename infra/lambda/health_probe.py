"""Checks the public /health endpoint and publishes the result as CloudWatch metrics.

Runs outside the VPC on a 5-minute schedule, so it sees what a real user sees: DNS, the
Elastic IP, TLS, Caddy, the app and the database all have to work for it to pass.
"""

import http.client
import json
import os
import time
import urllib.request

import boto3

cloudwatch = boto3.client("cloudwatch")
TIMEOUT_SECONDS = 10


def check(url: str) -> tuple[bool, float]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            body = json.load(response)
        healthy = response.status == 200 and body.get("status") == "ok"
    except (OSError, http.client.HTTPException, ValueError) as exc:
        print(f"Health check failed: {exc}")
        healthy = False
    return healthy, (time.perf_counter() - started) * 1000


def handler(event, context):
    healthy, latency_ms = check(os.environ["HEALTH_URL"])
    print(json.dumps({"healthy": healthy, "latency_ms": round(latency_ms, 1)}))
    cloudwatch.put_metric_data(
        Namespace="Roomly",
        MetricData=[
            {
                "MetricName": "HealthCheckOk",
                "Value": 1 if healthy else 0,
                "Unit": "Count",
            },
            {
                "MetricName": "HealthLatencyMs",
                "Value": latency_ms,
                "Unit": "Milliseconds",
            },
        ],
    )
    return {"healthy": healthy}
