FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN python -m pip install --no-cache-dir "fastapi==0.162.0" "uvicorn[standard]==0.54.0" "httpx==0.28.1"
COPY app.py .
RUN useradd -u 10001 app
USER 10001
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
