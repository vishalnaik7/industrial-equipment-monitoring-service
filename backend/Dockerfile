FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY data/reference ./data/reference
COPY data/seed ./data/seed
COPY scripts ./scripts
COPY sample_payloads ./sample_payloads
ENV DB_PATH=/data/monitoring.db
RUN useradd -r app && mkdir /data && chown app /data
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
