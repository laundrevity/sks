# backend/Dockerfile
FROM python:3.13-slim

# System deps (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

# Hermetic Python env via Pipenv (no network at runtime)
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir pipenv==2024.0.1

WORKDIR /srv

# First copy only Pipenv files to leverage Docker layer caching
COPY backend/Pipfile backend/Pipfile.lock ./

# Install into the system site-packages (no virtualenv) and fail if lock mismatches
RUN PIPENV_VENV_IN_PROJECT=0 \
    pipenv install --deploy --system

# Now copy the actual source
COPY backend/ ./

# Default runtime env
ENV PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO \
    PORT=8000 \
    HOST=0.0.0.0

EXPOSE 8000

# Start the aiohttp app
CMD ["python", "server.py"]

