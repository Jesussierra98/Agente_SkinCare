# Imagen del Runtime_Agente para AgentCore (ARM64, puerto 8080).
# Contexto de build = raíz del repositorio:
#   docker buildx build --platform linux/arm64 -f src/agent/Dockerfile -t ultra-skincare-agent .
FROM --platform=linux/arm64 python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PRODUCTION=true

# Misma disposición que el repositorio: `advisor/config.py` resuelve la guía en `<raíz>/src/etl/config/guide.json`.
WORKDIR /app/src/agent

COPY src/agent/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY src/agent/ ./
COPY src/etl/config/guide.json /app/src/etl/config/guide.json

# Sin privilegios de root.
RUN useradd --system --uid 10001 agent && chown -R agent /app
USER agent

EXPOSE 8080
CMD ["python", "agent.py"]
