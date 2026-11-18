# ==========================================
# Vectis Multi-Stage Alpine Containerfile
# Target size: < 65 MB
# ==========================================

# Stage 1: Build virtual environment and wheels
FROM python:3.12-alpine AS builder

WORKDIR /build

# Install minimal build tools for C extensions if required by dependencies
RUN apk add --no-cache gcc musl-dev libffi-dev

# Create isolated production virtualenv
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install packaging tools and core dependency
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip wheel setuptools && \
    pip install --no-cache-dir litellm>=1.0.0

# Copy source repository and install package
COPY . .
RUN pip install --no-cache-dir --no-deps . && \
    find /opt/venv -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /opt/venv -type f -name "*.pyc" -delete && \
    find /opt/venv -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true && \
    rm -rf /root/.cache

# ==========================================
# Stage 2: Minimal runtime image
# ==========================================
FROM python:3.12-alpine AS runner

LABEL maintainer="Vectis Core Architecture Team" \
      description="Ultra-lightweight multi-agent orchestration framework"

# Setup runtime environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    VECTIS_DB_PATH="/app/data/vectis.db" \
    VECTIS_WORKSPACES_DIR="/app/workspaces"

WORKDIR /app

# Copy virtualenv from builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy entrypoint script
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Create non-root user and persistent volume mount directories
RUN addgroup -g 1000 vectis && \
    adduser -u 1000 -G vectis -s /bin/sh -D vectis && \
    mkdir -p /app/data /app/workspaces && \
    chown -R vectis:vectis /app

USER vectis

# Volumes for persistent SQLite state and tool execution workspaces
VOLUME ["/app/data", "/app/workspaces"]

# Expose Web UI dashboard port
EXPOSE 8080

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["run"]
