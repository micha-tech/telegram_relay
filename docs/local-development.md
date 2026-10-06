# Local development

Use Python 3.11 or newer. Create a virtual environment, run `pip install -e '.[dev]'`, configure `.env`, then run `python manage.py migrate`. Start the API with `python manage.py runserver` and the worker separately with `python manage.py runtelegramworker`. Run checks with `pytest` and `ruff check .`.

For Aiven, copy its TLS PostgreSQL URI into `DATABASE_URL` (including its required `sslmode`). For Upstash, use the Redis TLS URI in `REDIS_URL`; do not use the REST URL. Local Compose supplies PostgreSQL and Redis for development.
