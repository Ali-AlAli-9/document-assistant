FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js for frontend build
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Build frontend
RUN cd frontend && npm install && npm run build && cd ..

RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["gunicorn", "rag_project.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "120"]
