from flask import (
    Flask,
    render_template,
    request,
    redirect,
    send_file,
    jsonify,
    Response,
)
import os
import json
import shutil
import queue
import time
import threading
from datetime import datetime, timedelta
from threading import Lock
from dotenv import load_dotenv

load_dotenv()

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
METADATA_FILE = os.getenv("METADATA_FILE", "uploaded.json")
FILE_LIFESPAN_HOURS = int(os.getenv("FILE_LIFESPAN_HOURS", "1"))
EXPIRY_CHECK_INTERVAL = int(os.getenv("EXPIRY_CHECK_INTERVAL", "30"))
FLASK_HOST = os.getenv("FLASK_RUN_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_RUN_PORT", "7111"))
FLASK_DEBUG = os.getenv("FLASK_ENV", "development") == "development"

app = Flask(__name__)
app.secret_key = os.urandom(24)
metadata_lock = Lock()

clients = []
clients_lock = Lock()


def load_metadata():
    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, "r") as f:
            return json.load(f)
    return {}


def save_metadata(data):
    with metadata_lock:
        with open(METADATA_FILE, "w") as f:
            json.dump(data, f, indent=2)


def broadcast_event(event_type, data=None):
    with clients_lock:
        dead_clients = []
        for client in clients:
            try:
                message = json.dumps({"type": event_type, "data": data})
                client.put(message)
            except:
                dead_clients.append(client)
        for client in dead_clients:
            clients.remove(client)


def cleanup_expired():
    metadata = load_metadata()
    now = datetime.now()
    to_delete = []

    for file_id, info in metadata.items():
        expires = datetime.fromisoformat(info["expires_at"])
        if now > expires:
            to_delete.append(file_id)

    for file_id in to_delete:
        filepath = os.path.join(UPLOAD_DIR, file_id)
        if os.path.exists(filepath):
            os.remove(filepath)
        del metadata[file_id]

    if to_delete:
        save_metadata(metadata)
        broadcast_event("refresh")

    return metadata


def expiry_checker():
    while True:
        time.sleep(EXPIRY_CHECK_INTERVAL)
        cleanup_expired()


def clear_all_on_startup():
    # Delete all files inside uploads directory (not the directory itself, for Docker volumes)
    if os.path.exists(UPLOAD_DIR):
        for filename in os.listdir(UPLOAD_DIR):
            filepath = os.path.join(UPLOAD_DIR, filename)
            try:
                if os.path.isfile(filepath):
                    os.remove(filepath)
            except Exception:
                pass
    
    # Delete metadata file
    if os.path.exists(METADATA_FILE):
        try:
            os.remove(METADATA_FILE)
        except Exception:
            pass
    
    # Create uploads directory if it doesn't exist
    os.makedirs(UPLOAD_DIR, exist_ok=True)


clear_all_on_startup()

checker_thread = threading.Thread(target=expiry_checker, daemon=True)
checker_thread.start()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/files")
def get_files():
    metadata = load_metadata()
    files = []

    for file_id, info in sorted(
        metadata.items(), key=lambda x: x[1]["uploaded_at"], reverse=True
    ):
        files.append(
            {
                "id": file_id,
                "filename": info["filename"],
                "uploaded_at": datetime.fromisoformat(info["uploaded_at"]).strftime(
                    "%Y-%m-%d %H:%M"
                ),
                "expires_at": datetime.fromisoformat(info["expires_at"]).strftime(
                    "%Y-%m-%d %H:%M"
                ),
            }
        )

    return jsonify(files)


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "no file"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "empty filename"}), 400

    file_id = os.urandom(16).hex()
    filepath = os.path.join(UPLOAD_DIR, file_id)
    file.save(filepath)

    now = datetime.now()
    expires = now + timedelta(hours=FILE_LIFESPAN_HOURS)

    metadata = load_metadata()
    metadata[file_id] = {
        "filename": file.filename,
        "uploaded_at": now.isoformat(),
        "expires_at": expires.isoformat(),
    }
    save_metadata(metadata)

    broadcast_event("refresh")
    return jsonify({"success": True})


@app.route("/events")
def events():
    client_queue = queue.Queue()

    with clients_lock:
        clients.append(client_queue)

    def generate():
        try:
            while True:
                message = client_queue.get()
                yield f"data: {message}\n\n"
        except GeneratorExit:
            with clients_lock:
                if client_queue in clients:
                    clients.remove(client_queue)

    return Response(generate(), mimetype="text/event-stream")


@app.route("/download/<file_id>")
def download(file_id):
    metadata = load_metadata()

    if file_id not in metadata:
        return "File not found or expired", 404

    expires = datetime.fromisoformat(metadata[file_id]["expires_at"])
    if datetime.now() > expires:
        return "File expired", 404

    filepath = os.path.join(UPLOAD_DIR, file_id)
    return send_file(
        filepath, as_attachment=True, download_name=metadata[file_id]["filename"]
    )


@app.route("/delete/<file_id>", methods=["POST"])
def delete(file_id):
    metadata = load_metadata()

    if file_id in metadata:
        filepath = os.path.join(UPLOAD_DIR, file_id)
        if os.path.exists(filepath):
            os.remove(filepath)
        del metadata[file_id]
        save_metadata(metadata)
        broadcast_event("refresh")

    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG, threaded=True)
