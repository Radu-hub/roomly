# Built from the repository root, because the image also needs db/:
#   docker build -f app/Dockerfile .
FROM python:3.12-slim-bookworm AS base

ARG APP_VERSION=dev
ARG GIT_SHA=dev
ARG BUILD_TIME=dev

ENV APP_VERSION=$APP_VERSION \
    GIT_SHA=$GIT_SHA \
    BUILD_TIME=$BUILD_TIME \
    DB_DIR=/app/db \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# psql runs the migrations.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The RDS certificate authorities, so sslmode=verify-full can check the database's identity.
ADD --chmod=644 https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem /app/certs/rds-global-bundle.pem

COPY app/pyproject.toml ./
COPY app/roomly ./roomly
COPY app/templates ./templates
COPY app/static ./static
COPY db ./db

RUN useradd --system --uid 1000 --no-create-home roomly

# ---- local development and tests: adds pytest and ruff ----
FROM base AS dev
RUN pip install --no-cache-dir -e '.[dev]'
COPY app/tests ./tests
USER roomly
CMD ["python", "-m", "roomly.main", "--reload"]

# ---- production: the last stage, so it's what a plain docker build produces ----
FROM base AS prod
RUN pip install --no-cache-dir .
USER roomly
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"
CMD ["python", "-m", "roomly.main"]
