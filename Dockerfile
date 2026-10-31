FROM python:3.12-slim
WORKDIR /app
COPY requirements-marketing.txt ./requirements-marketing.txt
RUN pip install --no-cache-dir -r requirements-marketing.txt
COPY runner ./runner
COPY workflows ./workflows
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python", "-m", "runner.worker"]
