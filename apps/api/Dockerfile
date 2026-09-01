FROM python:3.9-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements-api.lock ./
COPY apps/api ./apps/api
COPY benchmarks ./benchmarks
COPY contracts ./contracts
COPY protocols ./protocols
COPY skill_runtime ./skill_runtime
COPY skills ./skills

RUN pip install --no-cache-dir --require-hashes -r requirements-api.lock \
    && addgroup --system egoagentos \
    && adduser --system --ingroup egoagentos --home /nonexistent --no-create-home egoagentos \
    && mkdir -p /data \
    && chown -R egoagentos:egoagentos /data

USER egoagentos

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2).read()"]

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
