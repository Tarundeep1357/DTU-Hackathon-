FROM node:22.14.0-bookworm-slim AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng curl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN useradd --create-home --uid 10001 arena && mkdir -p /app/data && chown arena:arena /app/data
COPY --chown=arena:arena . .
COPY --from=ui --chown=arena:arena /ui/dist /app/frontend/dist
USER arena
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s CMD curl --fail http://localhost:8501/api/health || exit 1
CMD ["python", "-m", "uvicorn", "evidence_arena.api:app", "--host", "0.0.0.0", "--port", "8501"]

