# ============================================================
# TASO – Application Dockerfile
# ============================================================
FROM python:3.11-slim

LABEL maintainer="TASO Project"
LABEL description="Telegram Autonomous Security Operator"

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    docker.io \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir bandit safety pip-audit

# Copy application source
COPY . .

# Create data and log directories
RUN mkdir -p data logs

# Non-root user for security
RUN useradd -m -u 1000 taso \
    && chown -R taso:taso /app
USER taso

EXPOSE 8080

CMD ["python", "main.py"]
