FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

ENV HARBOUR_CONFIG=/config/config.json \
    PORT=8765 \
    PYTHONUNBUFFERED=1
EXPOSE 8765

HEALTHCHECK --interval=60s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/')" || exit 1

CMD ["python", "app.py"]
