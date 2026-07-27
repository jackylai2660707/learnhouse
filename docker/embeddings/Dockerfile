FROM python:3.12-slim

# fastembed pulls onnxruntime, tokenizers and numpy — all CPU-only and
# arm64-native. Deliberately NOT torch/sentence-transformers: those are several
# GB and this host has limited disk.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir \
         "fastembed==0.7.1" \
         "fastapi==0.115.6" \
         "uvicorn[standard]==0.34.0" \
         "pydantic==2.10.4"

# 768 native dims (matches CourseEmbedding's Vector(768)) and bilingual zh/en,
# which the course content needs. Override alongside EMBEDDINGS_DIMENSIONS —
# app.py refuses to start if the two disagree.
ARG EMBEDDINGS_MODEL=jinaai/jina-embeddings-v2-base-zh
ENV EMBEDDINGS_MODEL=${EMBEDDINGS_MODEL} \
    EMBEDDINGS_CACHE_PATH=/models \
    HF_HUB_DISABLE_PROGRESS_BARS=1

# Bake the weights into the image. The running container then never touches the
# network, so embeddings keep working if HuggingFace is unreachable or the host
# is offline, and startup does not stall on a ~640MB download.
RUN python -c "\
from fastembed import TextEmbedding; \
import os; \
TextEmbedding(model_name=os.environ['EMBEDDINGS_MODEL'], cache_dir='/models')" \
    && du -sh /models

WORKDIR /app
COPY app.py /app/app.py

EXPOSE 7997

# start-period covers loading the ONNX session into memory on a cold container.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:7997/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7997", "--workers", "1"]
