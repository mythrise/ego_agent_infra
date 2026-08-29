FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml requirements-api.lock ./
COPY apps ./apps
RUN pip install --no-cache-dir -r requirements-api.lock \
    && addgroup --system egoagentos \
    && adduser --system --ingroup egoagentos --home /nonexistent --no-create-home egoagentos \
    && mkdir -p /data \
    && chown -R egoagentos:egoagentos /data

ENV EGO_AGENTTEAMS_BRIDGE_DB=/data/bridge.sqlite3
VOLUME ["/data"]
USER egoagentos
EXPOSE 8010
CMD ["uvicorn", "apps.agentteams_bridge.main:app", "--host", "0.0.0.0", "--port", "8010"]
