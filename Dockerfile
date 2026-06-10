# Cloud Run image for the GameData backend (backend_stub).
#
# Build from the REPO ROOT so the backend_stub package keeps its package
# layout and appcast_server can import the single source of truth in
# src/oyster_agent_runner/release_channels.py (the per-directory
# backend_stub/Dockerfile flattens the package and breaks both imports).
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn python-multipart

COPY backend_stub/ backend_stub/
COPY src/oyster_agent_runner/ src/oyster_agent_runner/

ENV PYTHONPATH=/app/src

EXPOSE 8080

CMD ["uvicorn", "backend_stub.main:app", "--host", "0.0.0.0", "--port", "8080"]
