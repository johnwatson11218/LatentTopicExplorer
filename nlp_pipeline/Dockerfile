#FROM jupyter/datascience-notebook:latest
FROM python:3.11-slim

# Install system dependencies first
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements file and install Python dependencies
COPY requirements.txt . 
RUN pip install --no-cache-dir "numpy<2" && \
    pip install --no-cache-dir -r requirements.txt

# Copy your source code
COPY ./ ./

# Create data directories
RUN mkdir -p /app/data/pdfs /app/data/output

# Unbuffered Python output
ENV PYTHONUNBUFFERED=1

# RUN pip install --no-cache-dir -r /tmp/requirements.txt

# # Pre-download the model (this is smart!)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-mpnet-base-v2')"
RUN python -m spacy download en_core_web_sm

# Start the worker (assuming your script is named worker.py)
CMD ["python", "worker.py"]