# VPS and Docker

Install Docker, configure `.env`, run the migration release command, and start Compose. In production, point API and worker directly at Aiven and Upstash and omit local `db`/`redis`. Put Caddy in front of only the API (and separately the Next.js service); `deploy/Caddyfile` is a minimal example. Caddy terminates TLS and forwards HTTPS metadata. Keep worker replicas controlled to prevent duplicate account listeners; PostgreSQL still prevents duplicate publication claims.

