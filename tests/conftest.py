import io

import pytest

import app as drip


@pytest.fixture
def storage(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    data_dir = tmp_path / "data"
    monkeypatch.setattr(drip, "UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(drip, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(drip, "METADATA_FILE", str(data_dir / "metadata.json"))
    drip.init_storage()
    yield upload_dir, data_dir
    drip.metadata.clear()


@pytest.fixture
def client(storage):
    return drip.app.test_client()


@pytest.fixture
def upload(client):
    def _upload(*files, **form):
        data = {"file": [(io.BytesIO(body), name) for name, body in files], **form}
        return client.post("/upload", data=data, content_type="multipart/form-data")

    return _upload
