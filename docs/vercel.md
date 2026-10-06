# Vercel

Next.js can run on Vercel. The Telethon listener cannot: it requires durable connections and graceful long-running lifecycle. Prefer hosting the Django API and worker on a container/VPS platform from the beginning. If the Django API is adapted to a Vercel Python function, only short HTTP operations belong there; the worker remains elsewhere and both coordinate through PostgreSQL/Redis. Local disk is never session storage.

