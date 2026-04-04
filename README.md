# drip

A stupid simple file upload server with real-time updates and auto-expiration.

## Running

### Docker Compose

```yaml
services:
  drip:
    image: ghcr.io/bfpimentel/drip:latest
    container_name: drip
    restart: always
    ports:
      - "7123:7123"
    volumes:
      - ./uploads:/app/uploads
      - ./data:/app/data
```

### PWA Installation

The app can be installed as a PWA. Use a reverse proxy (nginx, traefik, caddy) with HTTPS to enable the install prompt.

## Screenshots

![drip.png](./resources/drip.png)
