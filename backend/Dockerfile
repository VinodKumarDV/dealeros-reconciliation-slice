FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "python -c \"from seed import ensure_schema_and_seed; ensure_schema_and_seed()\" && uvicorn app:app --host 0.0.0.0 --port 8000"]
