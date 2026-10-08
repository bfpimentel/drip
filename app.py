import json
import logging
import mimetypes
import os
import queue
import re
import threading
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
)
from waitress import serve

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
METADATA_FILE = os.path.join(DATA_DIR, "metadata.json")

FILE_LIFESPAN_HOURS = float(os.environ.get("FILE_LIFESPAN_HOURS", "1"))
MAX_UPLOAD_MB = float(os.environ.get("MAX_UPLOAD_MB", "0"))
PORT = int(os.environ.get("PORT", "7123"))

EXPIRY_CHECK_INTERVAL = 30
SSE_HEARTBEAT_INTERVAL = 15
# Small pastes ship with the file list so the page can copy them without a fetch.
TEXT_INLINE_LIMIT = 100 * 1024
# Each open tab holds one thread for its event stream, so leave plenty of room.
SERVER_THREADS = 32

DEFAULT_EXPIRY_MINUTES = max(1, round(FILE_LIFESPAN_HOURS * 60))
EXPIRY_CHOICES = sorted({10, 60, 24 * 60, 7 * 24 * 60, DEFAULT_EXPIRY_MINUTES})

FILE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

# SVG is left out because it can run scripts.
INLINE_TYPE_PREFIXES = ("image/", "video/", "audio/")
INLINE_TYPES = {"application/pdf"}
# Served as text/plain so markup is never rendered.
TEXT_LIKE_TYPES = {"application/json", "application/xml", "application/javascript"}

app = Flask(__name__)
if MAX_UPLOAD_MB > 0:
    app.config["MAX_CONTENT_LENGTH"] = int(MAX_UPLOAD_MB * 1024 * 1024)

# In-memory source of truth, persisted to METADATA_FILE on every change.
# Every read-modify-write must hold metadata_lock.
metadata = {}
metadata_lock = threading.Lock()

clients = set()
clients_lock = threading.Lock()


def utcnow():
    return datetime.now(timezone.utc)


def upload_path(file_id):
    return os.path.join(UPLOAD_DIR, file_id)


def remove_upload(file_id):
    with suppress(FileNotFoundError):
        os.remove(upload_path(file_id))


def is_expired(info, now):
    return datetime.fromisoformat(info["expires_at"]) <= now


def format_minutes(minutes):
    if minutes % (24 * 60) == 0:
        return f"{minutes // (24 * 60)}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def preview_mimetype(filename):
    mimetype, _ = mimetypes.guess_type(filename)
    if mimetype is None or mimetype == "image/svg+xml":
        return None
    if mimetype.startswith(INLINE_TYPE_PREFIXES) or mimetype in INLINE_TYPES:
        return mimetype
    if mimetype.startswith("text/") or mimetype in TEXT_LIKE_TYPES:
        return "text/plain; charset=utf-8"
    return None


def load_metadata():
    try:
        with open(METADATA_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        app.logger.warning("ignoring corrupt metadata file %s", METADATA_FILE)
        return {}


def save_metadata():
    tmp_file = f"{METADATA_FILE}.tmp"
    with open(tmp_file, "w") as f:
        json.dump(metadata, f, indent=2)
    os.replace(tmp_file, METADATA_FILE)


def broadcast_event(event_type, data=None):
    message = json.dumps({"type": event_type, "data": data})
    with clients_lock:
        for client in clients:
            client.put(message)


def live_entry(file_id):
    with metadata_lock:
        info = metadata.get(file_id)
    if info is None or is_expired(info, utcnow()):
        return None
    return info


def file_size(file_id, info):
    if "size" in info:
        return info["size"]
    try:
        return os.path.getsize(upload_path(file_id))
    except FileNotFoundError:
        return 0


def read_text(file_id):
    try:
        with open(upload_path(file_id), encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        return None


def add_entries(entries):
    with metadata_lock:
        metadata.update(entries)
        save_metadata()

    broadcast_event("refresh")


def new_entry(filename, file_id, kind, now, lifespan):
    return {
        "filename": filename,
        "kind": kind,
        "size": os.path.getsize(upload_path(file_id)),
        "uploaded_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + lifespan).isoformat(timespec="seconds"),
    }


def save_uploads(files, lifespan):
    files = [f for f in files if f.filename]
    if not files:
        return 0

    now = utcnow()
    entries = {}
    for file in files:
        file_id = os.urandom(16).hex()
        file.save(upload_path(file_id))
        entries[file_id] = new_entry(file.filename, file_id, "file", now, lifespan)

    add_entries(entries)
    return len(entries)


def save_text(text, lifespan):
    if not text.strip():
        return False

    now = utcnow()
    file_id = os.urandom(16).hex()
    with open(upload_path(file_id), "w", encoding="utf-8") as f:
        f.write(text)

    filename = f"paste-{now:%Y%m%d-%H%M%S}.txt"
    add_entries({file_id: new_entry(filename, file_id, "text", now, lifespan)})
    return True


def cleanup_expired():
    now = utcnow()
    with metadata_lock:
        expired = [fid for fid, info in metadata.items() if is_expired(info, now)]
        for file_id in expired:
            del metadata[file_id]
            remove_upload(file_id)
        if expired:
            save_metadata()

    if expired:
        broadcast_event("refresh")


def expiry_checker():
    while True:
        time.sleep(EXPIRY_CHECK_INTERVAL)
        cleanup_expired()


def init_storage():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    with metadata_lock:
        metadata.clear()
        metadata.update(load_metadata())

        for file_id in [
            fid for fid in metadata if not os.path.isfile(upload_path(fid))
        ]:
            del metadata[file_id]
        for name in os.listdir(UPLOAD_DIR):
            if FILE_ID_PATTERN.match(name) and name not in metadata:
                remove_upload(name)

        save_metadata()

    cleanup_expired()


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": f"upload exceeds {MAX_UPLOAD_MB:g} MB limit"}), 413


@app.route("/")
def index():
    return render_template(
        "index.html",
        expiry_choices=[(m, format_minutes(m)) for m in EXPIRY_CHOICES],
        default_expiry=DEFAULT_EXPIRY_MINUTES,
    )


@app.route("/healthz")
def healthz():
    return "ok"


@app.route("/sw.js")
def service_worker():
    # Served from the root so the worker's scope covers the whole app.
    return send_from_directory(app.static_folder, "sw.js", max_age=0)


@app.route("/api/files")
def get_files():
    now = utcnow()
    with metadata_lock:
        snapshot = list(reversed(metadata.items()))

    files = []
    for file_id, info in sorted(
        snapshot, key=lambda x: x[1]["uploaded_at"], reverse=True
    ):
        if is_expired(info, now):
            continue

        kind = info.get("kind", "file")
        size = file_size(file_id, info)
        item = {
            "id": file_id,
            "filename": info["filename"],
            "kind": kind,
            "size": size,
            "uploaded_at": info["uploaded_at"],
            "expires_at": info["expires_at"],
            "previewable": preview_mimetype(info["filename"]) is not None,
        }
        if kind == "text":
            item["text"] = read_text(file_id) if size <= TEXT_INLINE_LIMIT else None
        files.append(item)

    return jsonify(files)


@app.route("/upload", methods=["POST"])
def upload():
    minutes = request.form.get("expires_in", DEFAULT_EXPIRY_MINUTES, type=int)
    if minutes not in EXPIRY_CHOICES:
        return jsonify({"error": "invalid expiry"}), 400

    lifespan = timedelta(minutes=minutes)

    text = request.form.get("text")
    if text is not None:
        if not save_text(text, lifespan):
            return jsonify({"error": "empty text"}), 400
    elif not save_uploads(request.files.getlist("file"), lifespan):
        return jsonify({"error": "no file"}), 400

    return jsonify({"success": True})


@app.route("/share", methods=["POST"])
def share():
    # PWA share target.
    lifespan = timedelta(minutes=DEFAULT_EXPIRY_MINUTES)
    if not save_uploads(request.files.getlist("file"), lifespan):
        parts = []
        for field in ("title", "text", "url"):
            value = request.form.get(field, "").strip()
            # Some platforms repeat the link inside the text; keep it once.
            if value and not any(value in part for part in parts):
                parts.append(value)
        save_text("\n".join(parts), lifespan)

    return redirect("/", code=303)


@app.route("/events")
def events():
    client_queue = queue.Queue()

    with clients_lock:
        clients.add(client_queue)

    def generate():
        try:
            # Waitress only starts the response once the first chunk arrives.
            yield "retry: 3000\n\n"
            while True:
                try:
                    message = client_queue.get(timeout=SSE_HEARTBEAT_INTERVAL)
                except queue.Empty:
                    # Keeps proxies from timing out and detects disconnected clients.
                    yield ": ping\n\n"
                    continue
                yield f"data: {message}\n\n"
        finally:
            with clients_lock:
                clients.discard(client_queue)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/download/<file_id>")
def download(file_id):
    info = live_entry(file_id)
    if info is None:
        return "File not found or expired", 404

    try:
        return send_file(
            upload_path(file_id), as_attachment=True, download_name=info["filename"]
        )
    except FileNotFoundError:
        return "File not found or expired", 404


@app.route("/view/<file_id>")
def view(file_id):
    info = live_entry(file_id)
    mimetype = info and preview_mimetype(info["filename"])
    if not mimetype:
        return "File not found or not previewable", 404

    try:
        response = send_file(
            upload_path(file_id),
            mimetype=mimetype,
            as_attachment=False,
            download_name=info["filename"],
        )
    except FileNotFoundError:
        return "File not found or expired", 404

    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.route("/delete/<file_id>", methods=["POST"])
def delete(file_id):
    with metadata_lock:
        existed = metadata.pop(file_id, None) is not None
        if existed:
            remove_upload(file_id)
            save_metadata()

    if existed:
        broadcast_event("refresh")

    return jsonify({"success": True})


def main():
    logging.basicConfig(level=logging.INFO)
    init_storage()
    threading.Thread(target=expiry_checker, daemon=True).start()
    serve(app, host="0.0.0.0", port=PORT, threads=SERVER_THREADS)


if __name__ == "__main__":
    main()
