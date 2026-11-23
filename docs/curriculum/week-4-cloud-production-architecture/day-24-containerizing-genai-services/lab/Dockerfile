# syntax=docker/dockerfile:1
# ---------- stage 1: build the venv with uv (cached, reproducible) ----------
FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_NO_DEV=1
WORKDIR /app
# deps layer: only re-runs when the lockfile changes; cache mount survives builds
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

# ---------- stage 2: runtime (no uv, no compilers, non-root) ----------
FROM python:3.13-slim AS runtime
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app app ./app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER app
EXPOSE 8000
# slim has no curl: use the stdlib. Liveness only -> /healthz, never /readyz.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]
# exec form = uvicorn is PID 1 and receives SIGTERM directly
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--timeout-graceful-shutdown", "25", "--proxy-headers", "--no-access-log"]
