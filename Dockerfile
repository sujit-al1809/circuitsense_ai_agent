# syntax=docker/dockerfile:1
# Single container: FastAPI backend + built React frontend (served by the API).

# ---- frontend build ---------------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- runtime ----------------------------------------------------------------
FROM python:3.11-slim AS runtime
# Optional ngspice for the simulate_circuit tool: --build-arg INSTALL_NGSPICE=true
ARG INSTALL_NGSPICE=false
RUN if [ "$INSTALL_NGSPICE" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends ngspice && rm -rf /var/lib/apt/lists/*; \
    fi
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY data /app/data
COPY --from=frontend /src/frontend/dist /app/frontend/dist

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    FRONTEND_DIST=/app/frontend/dist \
    PROVIDER_MODE=demo \
    CORS_ORIGINS=""

RUN useradd --create-home --uid 10001 circuitsense
USER circuitsense
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=3).status == 200 else 1)"
# One worker: sessions are kept in process memory (see README → Limitations).
# PORT is provided by platforms such as Render; it defaults to 8000 (ECS, local docker run).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
