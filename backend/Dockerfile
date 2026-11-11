FROM python:3.11-slim

WORKDIR /app

# Install system deps needed by psycopg2 and shapely
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc libgeos-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
