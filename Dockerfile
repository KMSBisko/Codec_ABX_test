# ── STAGE 1: Builder ─────────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install system deps needed for building
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements-web.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements-web.txt

# ── STAGE 2: Runtime ─────────────────────────────────────────────
FROM python:3.11-slim AS final

WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /install /usr/local

# Install ffmpeg for audio processing
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl && \
    rm -rf /var/lib/apt/lists/*

# Copy application code
COPY web/ ./web/
COPY app/ ./app/

# Create runtime directories
RUN mkdir -p /app/web/uploads /app/web/sessions

# Non-root user for security
RUN adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD curl -f http://localhost:5000/api/health || exit 1

CMD ["python", "-m", "web.app"]