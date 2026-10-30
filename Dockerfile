FROM python:3.12-slim
WORKDIR /app
COPY runner ./runner
COPY workflows ./workflows
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python", "-m", "runner.worker"]
