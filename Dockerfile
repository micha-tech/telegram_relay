FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN addgroup --system app && adduser --system --ingroup app app
COPY . .
RUN pip install --no-cache-dir .
RUN chown -R app:app /app
USER app
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "gunicorn config.asgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --worker-class uvicorn.workers.UvicornWorker --timeout 30 --graceful-timeout 30 --access-logfile -"]
