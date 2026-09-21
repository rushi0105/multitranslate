import io
import time
import zipfile

import pytest

import web as webmod

HDR = {"X-Requested-With": "fetch"}


@pytest.fixture
def client(tmp_path, monkeypatch, cache_path):
    monkeypatch.setattr(webmod, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(webmod, "HISTORY_FILE", tmp_path / "jobs" / "history.json")
    monkeypatch.setattr(webmod, "ALLOWED_ROOTS", [])
    webmod.JOBS.clear()
    original = webmod.JobOptions.__init__

    def patched(self, *a, **kw):                     # every job uses the temp cache
        kw["cache_path"] = cache_path
        original(self, *a, **kw)
    monkeypatch.setattr(webmod.JobOptions, "__init__", patched)
    webmod.app.config["TESTING"] = True
    return webmod.app.test_client()


def wait_done(client, job_id, timeout=15):
    for _ in range(int(timeout / 0.1)):
        s = client.get(f"/jobs/{job_id}").get_json()
        if s["state"] != "running":
            return s
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_index_renders(client):
    r = client.get("/")
    assert r.status_code == 200 and b"MultiTranslate" in r.data and "ਪੰਜਾਬੀ".encode() in r.data
    assert client.get("/manifest.json").get_json()["name"] == "MultiTranslate"


def test_csrf_header_required(client):
    assert client.post("/jobs", data={"targets": "hi"}).status_code == 403
    r = client.post("/jobs", data={"targets": "hi"}, headers={**HDR, "Origin": "http://evil.example"})
    assert r.status_code == 403


def test_upload_job_zip_and_history(client):
    data = {"targets": "hi,pa", "mode": "combined",
            "files": [(io.BytesIO(b"Hello"), "folder/a.txt"), (io.BytesIO(b"Bye"), "b.txt")]}
    r = client.post("/jobs", data=data, headers=HDR, content_type="multipart/form-data")
    assert r.status_code == 200
    job_id = r.get_json()["id"]
    s = wait_done(client, job_id)
    assert s["state"] == "done" and sorted(s["outputs"]) == ["b_multi.txt", "folder/a_multi.txt"]
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/jobs/{job_id}/download").data))
    assert sorted(z.namelist()) == ["b_multi.txt", "folder/a_multi.txt"]
    assert "[pa] Hello" in z.read("folder/a_multi.txt").decode("utf-8")
    assert client.get(f"/jobs/{job_id}/files/b_multi.txt").status_code == 200
    assert client.get(f"/jobs/{job_id}/files/../input/b.txt").status_code == 404
    hist = client.get("/jobs").get_json()
    assert hist[0]["id"] == job_id and hist[0]["state"] == "done"
    assert webmod.HISTORY_FILE.exists()


def test_local_path_job_stop_and_resume(client, tmp_path):
    src = tmp_path / "src"; src.mkdir()
    for i in range(3):
        (src / f"{i}.txt").write_text("x", encoding="utf-8")
    r = client.post("/jobs", data={"targets": "hi", "local_path": str(src)}, headers=HDR)
    job_id = r.get_json()["id"]
    s = wait_done(client, job_id)
    assert s["state"] == "done" and s["files_done"] == 3
    assert client.post(f"/jobs/{job_id}/resume", headers=HDR).status_code == 200
    s2 = wait_done(client, job_id)
    assert s2["state"] == "done" and s2["requests_made"] == 0          # everything skipped / cached


def test_validation_errors(client, tmp_path):
    assert client.post("/jobs", data={"targets": ""}, headers=HDR).status_code == 400
    assert client.post("/jobs", data={"targets": "hi", "local_path": str(tmp_path / "nope")}, headers=HDR).status_code == 400
    assert client.post("/jobs", data={"targets": "hi", "mode": "weird", "local_path": str(tmp_path)}, headers=HDR).status_code == 400
    assert client.get("/jobs/doesnotexist").status_code == 404


def test_allowed_roots_enforced(client, tmp_path, monkeypatch):
    monkeypatch.setattr(webmod, "ALLOWED_ROOTS", [tmp_path / "allowed"])
    (tmp_path / "allowed").mkdir(); (tmp_path / "other").mkdir()
    (tmp_path / "other" / "x.txt").write_text("x", encoding="utf-8")
    r = client.post("/jobs", data={"targets": "hi", "local_path": str(tmp_path / "other")}, headers=HDR)
    assert r.status_code == 403


def test_delete_job(client):
    r = client.post("/jobs", data={"targets": "hi", "files": [(io.BytesIO(b"Hi"), "a.txt")]},
                    headers=HDR, content_type="multipart/form-data")
    job_id = r.get_json()["id"]
    wait_done(client, job_id)
    assert client.delete(f"/jobs/{job_id}", headers=HDR).status_code == 200
    assert client.get(f"/jobs/{job_id}").status_code == 404
    assert not (webmod.JOBS_DIR / job_id).exists()
