FROM python:3.12-slim
WORKDIR /app
COPY requirements-marketing.txt ./requirements-marketing.txt
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements-marketing.txt
RUN mkdir -m 0700 /data && chown 10001:10001 /data
COPY runner ./runner
COPY workflows ./workflows
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python", "-m", "runner.worker"]
