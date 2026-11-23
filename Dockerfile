# syntax=docker/dockerfile:1.7
# Multi-stage: build a wheel with build tools, run on a slim image as a non-root user.
FROM python:3.13-slim AS build
WORKDIR /src
RUN pip install --no-cache-dir --upgrade pip build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m build --wheel --outdir /dist

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build /dist/*.whl /tmp/
RUN pip install /tmp/*.whl && rm -f /tmp/*.whl
COPY --chown=app:app config ./config
COPY --chown=app:app data ./data
ENV AIARCH_PRICES_FILE=/app/config/prices.json AIARCH_SEED_FOLDER=/app/data/sample_docs
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=2).status==200 else 1)"
CMD ["uvicorn", "aiarch.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
