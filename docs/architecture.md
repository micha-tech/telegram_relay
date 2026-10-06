# Architecture

The repository was initially empty. The backend uses Django 5 and Django REST Framework because the requirements favor one cohesive application with strong ORM constraints, migrations, authentication, authorization, validation, throttling, and operational middleware. This is not a permanent Telethon process hidden inside an HTTP server.

```text
Next.js -> HTTPS -> Django API -> Aiven PostgreSQL
                         |       -> Upstash Redis (locks/rates/cache only)
                         |
                    short Telegram login calls

Telegram MTProto <-> Telethon worker -> processing pipeline -> PostgreSQL
                         |                    |
                         +---- publish new destination message
```

The API and worker share models and services but are separate OS processes. `runtelegramworker` loads encrypted durable sessions, registers one handler per account, claims each `(account, source chat, source message, relay)` in PostgreSQL, transforms it, publishes a new message, and records the result. The unique constraint makes reconnect/retry delivery idempotent.

## Components

- `relay/views.py`: thin authenticated HTTP boundary with consistent envelopes.
- `relay/models.py`: ownership, Telegram accounts/entities, encrypted sessions/challenges, relays, processing outcomes, audit records.
- `relay/services/telegram_auth.py`: persisted code/2FA state machine.
- `relay/services/session_store.py`: encrypted durable session abstraction.
- `relay/services/processing.py`: pure, independently testable transformation pipeline.
- `relay/services/redis.py`: provider-neutral coordination boundary.
- `relay/services/worker.py`: long-lived MTProto listener/publisher.

The framework is synchronous at its public WSGI boundary; Telethon operations have explicit async boundaries. A future queue can be inserted behind a service interface without changing HTTP contracts or the processing pipeline.

