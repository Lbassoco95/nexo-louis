FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencias del sistema (curl para healthcheck, build-essential por si hay wheels)
RUN apt-get update \
  && apt-get install -yq --no-install-recommends \
      curl ca-certificates build-essential \
  && rm -rf /var/lib/apt/lists/*

# Copia requirements primero para aprovechar cache
ARG REPO_DIR=server
COPY ${REPO_DIR}/requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt \
  && pip install "uvicorn[standard]==0.30.6" "gunicorn==22.0.0"

# Código completo del repo
COPY ${REPO_DIR}/ /app/

# Usuario no-root
RUN useradd -u 1000 -m kawiil && chown -R kawiil:kawiil /app
USER kawiil

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || curl -fsS http://127.0.0.1:8000/api/agents || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
