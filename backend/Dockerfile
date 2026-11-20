# One image for both the API and the worker; docker-compose.yml sets the command.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/app
WORKDIR /app

# Install only the dependencies listed in pyproject.toml first, so code changes do not
# invalidate this layer. The code itself is run from /app (PYTHONPATH), not installed.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt

COPY . .
RUN useradd --create-home appuser && mkdir -p /data/storage && chown appuser /data/storage
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
