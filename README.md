# drip

A stupid simple file upload server with real-time updates and auto-expiration.

## Configuration

Copy `.env.example` to `.env` and modify:

```bash
cp .env.example .env
```

Available variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_RUN_HOST` | `0.0.0.0` | Server bind address |
| `FLASK_RUN_PORT` | `7111` | Server port |
| `FLASK_ENV` | `development` | development/production |
| `FILE_LIFESPAN_HOURS` | `1` | File expiration time |
| `EXPIRY_CHECK_INTERVAL` | `30` | Seconds between expiry checks |

## Docker Compose

```yaml
services:
  drip:
    image: ghcr.io/bfpimentel/drip:latest
    container_name: drip
    restart: unless-stopped
    ports:
      - "7111:7111"
    env_file: .env
    volumes:
      - ./uploads:/app/uploads
      - ./data:/app/data
```

## PWA Installation

The app can be installed as a PWA. Use a reverse proxy (nginx, traefik, caddy) with HTTPS to enable the install prompt.

## License

MIT
