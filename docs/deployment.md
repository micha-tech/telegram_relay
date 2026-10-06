# Deployment

Apply migrations as a release step before starting processes. Run the API with Gunicorn and run exactly one `python manage.py runtelegramworker` deployment unless account partitioning is added. Configure Aiven/Upstash TLS URLs, restrictive `ALLOWED_HOSTS` and `CORS_ORIGINS`, strong independent app/JWT/encryption secrets, centralized JSON logs, and backups. Health checks target `/health/`.

Never bake `.env`, Telethon session data, Telegram codes, or database/Redis credentials into images. The Docker image uses a non-root user and handles SIGTERM in the worker.

