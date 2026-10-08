<p align="center">
  <img src="./static/icon.svg" alt="drip logo" width="96" />
</p>

<h1 align="center">drip</h1>

<p align="center">
  A stupid simple self-hosted drop for files and text, with real-time updates and auto-expiration.
</p>

- Drop, paste or pick files (several at once) with upload progress
- Paste plain text to keep it as a note, and copy it back with one click
- Choose how long each item lives; the list drains as it nears expiry
- Copy a download link, or preview images, video, audio, PDFs and text in the browser
- Share files, text and links straight from your phone when installed as a PWA
- Every open tab updates live

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

Items are removed automatically once they expire. Uploads and their metadata survive restarts as long as both volumes are mounted.

The container runs as UID 1000, so the mounted directories must be writable by it (`chown -R 1000:1000 uploads data`). With rootless Podman, add `:U` to the volume options instead (e.g. `./uploads:/app/uploads:U`).

### Configuration

| Variable              | Default         | Description                                 |
| --------------------- | --------------- | ------------------------------------------- |
| `FILE_LIFESPAN_HOURS` | `1`             | Default lifespan (fractions allowed)        |
| `MAX_UPLOAD_MB`       | `0` (unlimited) | Maximum size of a single upload request     |
| `PORT`                | `7123`          | Port to listen on                           |
| `UPLOAD_DIR`          | `./uploads`     | Where uploaded files are stored             |
| `DATA_DIR`            | `./data`        | Where file metadata (`metadata.json`) lives |

Paths are relative to `app.py`, which is `/app` inside the container. Besides the default, uploads can be set to expire in 10m, 1h, 1d or 7d.

### Local development

Requires [uv](https://docs.astral.sh/uv/):

```sh
uv sync
uv run app.py
```

Then open http://localhost:7123. Run the checks with:

```sh
uv run pytest
uv run ruff check
uv run ruff format --check
```

### PWA Installation

The app can be installed as a PWA. Use a reverse proxy (nginx, traefik, caddy) with HTTPS to enable the install prompt. Once installed, drip shows up in the system share sheet (on platforms that support Web Share Target, e.g. Android). Shared files, text and links are saved with the default lifespan.

## Screenshots

<p align="center">
  <img src="./resources/drip.png" alt="drip on desktop: upload card, lifespan picker and a list of files and a pasted note with expiry bars" width="800" />
  <br />
  <em>desktop</em>
</p>

<p align="center">
  <img src="./resources/drip-mobile.png" alt="drip on a phone: tap to upload, lifespan picker and the item list" width="320" />
  <br />
  <em>mobile</em>
</p>

## Credits

Bundles the [Iosevka](https://github.com/be5invis/Iosevka) and [Geist](https://github.com/vercel/geist-font) fonts, both under the SIL Open Font License (see `static/fonts/`).

## AI Disclaimer

The core part of the app (server) has been written by hand, but an AI agent has been used to develop the frontend (web page). All the code is human-reviewed before being pushed.
