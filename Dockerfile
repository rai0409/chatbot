FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Runtime data (vectorstore/, index/, data/, runs/) is mounted as volumes;
# nothing stateful is baked into the image (see .dockerignore).
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/vectorstore /app/index /app/data /app/runs \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "webapi.main:app", "--host", "0.0.0.0", "--port", "8000"]
