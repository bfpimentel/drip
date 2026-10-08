import io
import json
import queue
from datetime import datetime, timedelta

import app as drip


def only_file(client):
    files = client.get("/api/files").get_json()
    assert len(files) == 1
    return files[0]


def expire(file_id):
    drip.metadata[file_id]["expires_at"] = (
        drip.utcnow() - timedelta(seconds=1)
    ).isoformat()


def test_healthz(client):
    assert client.get("/healthz").data == b"ok"


def test_index_lists_expiry_choices(client):
    html = client.get("/").get_data(as_text=True)
    for minutes in drip.EXPIRY_CHOICES:
        assert f'value="{minutes}"' in html


def test_upload_and_list(client, upload, storage):
    upload_dir, _ = storage
    assert upload(("notes.txt", b"hello")).status_code == 200

    file = only_file(client)
    assert file["filename"] == "notes.txt"
    assert file["previewable"] is True
    assert (upload_dir / file["id"]).read_bytes() == b"hello"

    lifespan = datetime.fromisoformat(file["expires_at"]) - datetime.fromisoformat(
        file["uploaded_at"]
    )
    assert lifespan == timedelta(minutes=drip.DEFAULT_EXPIRY_MINUTES)


def test_upload_multiple(client, upload):
    upload(("a.txt", b"a"), ("b.txt", b"b"))
    files = client.get("/api/files").get_json()
    assert sorted(f["filename"] for f in files) == ["a.txt", "b.txt"]


def test_same_second_uploads_listed_newest_first(client, upload):
    upload(("first.txt", b"1"))
    upload(("second.txt", b"2"))
    files = client.get("/api/files").get_json()
    assert [f["filename"] for f in files] == ["second.txt", "first.txt"]


def test_upload_without_file(client, upload):
    assert upload().status_code == 400
    assert client.get("/api/files").get_json() == []


def test_upload_with_expiry_choice(client, upload):
    assert upload(("a.txt", b"a"), expires_in="10").status_code == 200
    file = only_file(client)
    lifespan = datetime.fromisoformat(file["expires_at"]) - datetime.fromisoformat(
        file["uploaded_at"]
    )
    assert lifespan == timedelta(minutes=10)


def test_upload_rejects_unknown_expiry(client, upload):
    response = upload(("a.txt", b"a"), expires_in="3")
    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid expiry"}


def test_upload_too_large(client, upload, monkeypatch):
    monkeypatch.setitem(drip.app.config, "MAX_CONTENT_LENGTH", 100)
    monkeypatch.setattr(drip, "MAX_UPLOAD_MB", 0.0001)
    response = upload(("big.bin", b"x" * 1000))
    assert response.status_code == 413
    assert "limit" in response.get_json()["error"]


def test_no_limit_by_default():
    assert drip.app.config["MAX_CONTENT_LENGTH"] is None


def test_download(client, upload):
    upload(("report.pdf", b"%PDF"))
    file = only_file(client)
    response = client.get(f"/download/{file['id']}")
    assert response.data == b"%PDF"
    assert "attachment" in response.headers["Content-Disposition"]
    assert "report.pdf" in response.headers["Content-Disposition"]


def test_download_unknown(client):
    assert client.get("/download/" + "0" * 32).status_code == 404


def test_view_text_is_plain_inline(client, upload):
    upload(("page.html", b"<script>alert(1)</script>"))
    file = only_file(client)
    response = client.get(f"/view/{file['id']}")
    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment" not in response.headers.get("Content-Disposition", "")


def test_view_image(client, upload):
    upload(("photo.png", b"\x89PNG"))
    file = only_file(client)
    assert client.get(f"/view/{file['id']}").mimetype == "image/png"


def test_view_rejects_unsafe_and_unknown_types(client, upload):
    upload(("logo.svg", b"<svg/>"), ("archive.zip", b"PK"))
    for file in client.get("/api/files").get_json():
        assert file["previewable"] is False
        assert client.get(f"/view/{file['id']}").status_code == 404


def test_delete(client, upload, storage):
    upload_dir, _ = storage
    upload(("a.txt", b"a"))
    file = only_file(client)

    assert client.post(f"/delete/{file['id']}").status_code == 200
    assert client.get("/api/files").get_json() == []
    assert not (upload_dir / file["id"]).exists()


def test_delete_unknown_is_ok(client):
    assert client.post("/delete/" + "0" * 32).status_code == 200


def test_expired_files_are_hidden_and_cleaned_up(client, upload, storage):
    upload_dir, _ = storage
    upload(("a.txt", b"a"))
    file = only_file(client)
    expire(file["id"])

    assert client.get("/api/files").get_json() == []
    assert client.get(f"/download/{file['id']}").status_code == 404

    drip.cleanup_expired()
    assert file["id"] not in drip.metadata
    assert not (upload_dir / file["id"]).exists()


def test_metadata_persists_across_restart(client, upload, storage):
    _, data_dir = storage
    upload(("a.txt", b"a"))
    file = only_file(client)
    assert file["id"] in json.loads((data_dir / "metadata.json").read_text())

    drip.metadata.clear()
    drip.init_storage()
    assert only_file(client)["id"] == file["id"]


def test_restart_cleans_up_inconsistent_state(client, upload, storage):
    upload_dir, _ = storage
    upload(("kept.txt", b"k"), ("lost.txt", b"l"), ("old.txt", b"o"))
    ids = {f["filename"]: f["id"] for f in client.get("/api/files").get_json()}

    (upload_dir / ids["lost.txt"]).unlink()
    expire(ids["old.txt"])
    drip.save_metadata()
    orphan = upload_dir / ("f" * 32)
    orphan.write_bytes(b"orphan")
    unrelated = upload_dir / "notes.txt"
    unrelated.write_bytes(b"not ours")

    drip.init_storage()

    assert set(drip.metadata) == {ids["kept.txt"]}
    assert not (upload_dir / ids["old.txt"]).exists()
    assert not orphan.exists()
    assert unrelated.exists()


def test_corrupt_metadata_is_ignored(storage):
    _, data_dir = storage
    (data_dir / "metadata.json").write_text("{not json")
    drip.init_storage()
    assert drip.metadata == {}


def test_share_target(client, storage):
    response = client.post(
        "/share",
        data={"file": (io.BytesIO(b"shared"), "shared.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 303
    assert response.headers["Location"] == "/"
    assert only_file(client)["filename"] == "shared.txt"


def test_share_without_files_redirects(client):
    response = client.post("/share", data={}, content_type="multipart/form-data")
    assert response.status_code == 303


def test_changes_are_broadcast(client, upload):
    listener = queue.Queue()
    drip.clients.add(listener)
    try:
        upload(("a.txt", b"a"))
        assert json.loads(listener.get_nowait())["type"] == "refresh"

        client.post(f"/delete/{only_file(client)['id']}")
        assert json.loads(listener.get_nowait())["type"] == "refresh"
    finally:
        drip.clients.discard(listener)


def test_service_worker_served_from_root(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "javascript" in response.mimetype


def test_format_minutes():
    assert drip.format_minutes(10) == "10m"
    assert drip.format_minutes(90) == "90m"
    assert drip.format_minutes(120) == "2h"
    assert drip.format_minutes(7 * 24 * 60) == "7d"
