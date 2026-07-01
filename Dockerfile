# =============================================================================
# TradingAgents — RQ Worker Dockerfile
# =============================================================================
# Build stage
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY . .
RUN pip install --no-cache-dir .

# Remove the server and cli packages from site-packages so that the
# runtime volume mount (./server, ./cli) takes precedence at import time.
RUN rm -rf /opt/venv/lib/python*/site-packages/server \
 && rm -rf /opt/venv/lib/python*/site-packages/cli

# ---------------------------------------------------------------------------
# Runtime stage
# ---------------------------------------------------------------------------
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

COPY --from=builder /opt/venv /opt/venv

# Create unprivileged user + reports directory
RUN useradd --create-home appuser \
 && install -d -m 0755 -o appuser -g appuser /home/appuser/.tradingagents \
 && install -d -m 0755 -o appuser -g appuser /home/appuser/app/reports

USER appuser
WORKDIR /home/appuser/app

COPY --from=builder --chown=appuser:appuser /build .

# 默认启动 RQ Worker，消费 trading-tasks 队列
CMD ["rq", "worker", "trading-tasks"]
