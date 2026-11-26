FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopíruje pouze aplikační kód a statické soubory (složka data/ je vyloučena v .dockerignore)
COPY . .

# Připraví prázdnou strukturu pro mount perzistentního volume
RUN mkdir -p /app/data/latina /app/data/rectina /app/data/balicky

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]