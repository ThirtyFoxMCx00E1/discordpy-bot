FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# accounts.db lives here; mount a volume so it survives redeploys
ENV DATA_DIR=/data
VOLUME /data

CMD ["python", "main.py"]
