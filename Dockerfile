FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --no-deps .
USER 10001:10001
ENTRYPOINT ["python", "-m", "arbledger"]
CMD ["--workers", "2", "--queue-size", "8"]
