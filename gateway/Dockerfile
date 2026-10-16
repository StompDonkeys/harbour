FROM python:3.12-slim

# yt-dlp needs a JavaScript runtime to read YouTube's player code.
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

# HOME/caches point at /tmp because the container runs as your limited NAS user.
ENV HARBOUR_CONFIG=/config/config.json \
    PORT=8765 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    DENO_DIR=/tmp/.deno
EXPOSE 8765

HEALTHCHECK --interval=60s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/')" || exit 1

CMD ["python", "app.py"]
