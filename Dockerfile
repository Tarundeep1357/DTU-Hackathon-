FROM python:3.11.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng curl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN useradd --create-home --uid 10001 arena && mkdir -p /app/data && chown arena:arena /app/data
COPY --chown=arena:arena . .
USER arena
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s CMD curl --fail http://localhost:8501/_stcore/health || exit 1
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address", "0.0.0.0"]

