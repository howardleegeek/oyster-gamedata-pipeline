# Multi-stage build for Oyster Dashboard
# Stage 1: Build Python dependencies
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt

# Stage 2: Production image
FROM python:3.11-slim as production

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 appuser

# Copy wheels from builder
COPY --from=builder /app/wheels /wheels
RUN pip install --no-cache-dir /wheels/*

# Copy application code
COPY server.py .
COPY app.py .

# Create data directory
RUN mkdir -p /data/sessions && chown -R appuser:appuser /app /data

# Switch to non-root user
USER appuser

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    API_BASE=http://localhost:8000 \
    JWT_SECRET=change-me-in-production

# Expose ports
# 8000 for FastAPI
# 8501 for Streamlit
EXPOSE 8000 8501

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command runs FastAPI
# For Streamlit, use: streamlit run app.py --server.port 8501
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]