# Pin the base image by digest for reproducible, reviewable builds:
#   docker pull python:3.12-slim
#   docker inspect --format '{{index .RepoDigests 0}}' python:3.12-slim
#   docker build --build-arg PYTHON_IMAGE=python:3.12-slim@sha256:<digest> .
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    HF_HOME=/app/models_cache \
    APP_ENV=production

WORKDIR /app

# Document extraction (stage 10, libs/docextract):
#   EXTRACTION=light  Tesseract (jpn, jpn_vert) + pdfplumber/pypdfium2 (default)
#   EXTRACTION=best   + Docling (TableFormer) and PaddleOCR 3.7 (PP-OCRv6); large image
# PDF reading never uses PyMuPDF (AGPL-3.0).
#   EXTRACTION=none   no OCR/table extras
# WITH_LIBREOFFICE=true adds LibreOffice for EMF/WMF images in Office files.
ARG EXTRACTION=light
ARG WITH_LIBREOFFICE=false
RUN set -eux; \
    pkgs=""; \
    if [ "$EXTRACTION" != "none" ]; then pkgs="$pkgs tesseract-ocr tesseract-ocr-jpn tesseract-ocr-jpn-vert tesseract-ocr-eng"; fi; \
    if [ "$WITH_LIBREOFFICE" = "true" ]; then pkgs="$pkgs libreoffice-core libreoffice-draw"; fi; \
    if [ -n "$pkgs" ]; then apt-get update && apt-get install -y --no-install-recommends $pkgs && rm -rf /var/lib/apt/lists/*; fi

# Python packages, version-locked by constraints/ (stage 13): the base set,
# Sudachi (keyword search), Redis/Postgres (shared state for several nodes) and
# Qdrant, so every backend in .env.example works with this image.
#   WITH_LOCAL_MODELS=true  sentence-transformers + CPU-only torch for the local
#                           embedding (EMBED_PROVIDER=local, ruri-v3) and the
#                           cross-encoder reranker (default)
#   WITH_LOCAL_MODELS=false only with EMBED_PROVIDER=openai and no reranker
# With the hash locks (scripts/lock_dependencies.sh) every file is checked
# against its recorded sha256; a changed or substituted package fails the build.
ARG WITH_LOCAL_MODELS=true
COPY requirements*.txt ./
COPY constraints/ constraints/
RUN set -eux; \
    cpu_index="--extra-index-url https://download.pytorch.org/whl/cpu"; \
    if [ -f requirements.lock.txt ]; then \
      if [ "$WITH_LOCAL_MODELS" = "true" ]; then \
        pip install --no-cache-dir --require-hashes -r requirements-embedding.lock.txt $cpu_index; \
      else \
        pip install --no-cache-dir --require-hashes -r requirements.lock.txt; \
      fi; \
    else \
      extra=""; \
      if [ "$WITH_LOCAL_MODELS" = "true" ]; then \
        extra="-r requirements-embedding.txt -c constraints/linux-py312-embedding.txt $cpu_index"; \
      fi; \
      pip install --no-cache-dir -r requirements.txt -r requirements-ja.txt -r requirements-state.txt -r requirements-qdrant.txt \
        -c constraints/linux-py312.txt $extra; \
    fi

COPY . .
# best is not version-locked: PaddleOCR needs numpy<2.4 (constraints/ pins a
# newer numpy), so pip moves numpy down. Record the versions of a best image.
RUN set -eux; \
    case "$EXTRACTION" in \
      light) pip install --no-cache-dir -e "libs/docextract[light]" -c constraints/linux-py312.txt ;; \
      best) pip install --no-cache-dir -e "libs/docextract[light,docling,paddle]" ;; \
      none) pip install --no-cache-dir -e libs/docextract ;; \
    esac

# Runtime data (vectorstore/, index/, data/, runs/, models_cache/) is mounted
# as volumes; nothing stateful is baked into the image (see .dockerignore).
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/vectorstore /app/index /app/data /app/runs /app/models_cache \
    && chown -R appuser:appuser /app/vectorstore /app/index /app/data /app/runs /app/models_cache
USER appuser

EXPOSE 8000

# --proxy-headers + --forwarded-allow-ips: client addresses come from the TLS
# proxy in front (needed for per-IP rate limits). Only the proxy's address
# may set X-Forwarded-*; set FORWARDED_ALLOW_IPS to it.
# METRICS_MULTIPROC_DIR (several workers): stale counter files of the previous
# run are cleared at start, so totals restart with the service.
# WEB_CONCURRENCY sets the number of uvicorn workers (default 1).
CMD ["sh", "-c", "if [ -n \"$METRICS_MULTIPROC_DIR\" ]; then mkdir -p \"$METRICS_MULTIPROC_DIR\" && find \"$METRICS_MULTIPROC_DIR\" -name '*.json' -type f -delete; fi; exec uvicorn webapi.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-1} --proxy-headers --forwarded-allow-ips=${FORWARDED_ALLOW_IPS:-127.0.0.1} --no-server-header"]
