"""MultiTranslate Web UI - run `python web.py` and open http://127.0.0.1:5055

Jobs live under ./jobs/<id>/{input,output,glossary}. Uploaded files are copied to input/; a local path
can be given instead (no upload) for big folders. Progress is polled from /jobs/<id>.
History is persisted in ./jobs/history.json so finished jobs survive a restart and can be resumed.

Security (local tool, bound to 127.0.0.1 only):
  * state-changing requests must carry `X-Requested-With: fetch` and a same-origin Origin header, so a
    malicious web page open in the same browser cannot drive the tool (CSRF).
  * upload names are sanitised, downloads are confined to the job's output folder.
  * MT_ALLOWED_ROOTS (";"-separated) restricts which local paths may be translated. Unset = any path.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import socket
import sys
import threading
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from mt.engine import DEFAULT_AUTOSAVE, DEFAULT_RPS, DEFAULT_WORKERS
from mt.languages import FEATURED, LANGUAGES, NATIVE_NAMES, parse_language_list
from mt.runner import JobOptions, JobStatus, run_job

ROOT = Path(__file__).resolve().parent
JOBS_DIR = ROOT / "jobs"
HISTORY_FILE = JOBS_DIR / "history.json"
PORT = int(os.environ.get("MT_PORT", 5055))
# --lan / MT_LAN=1 binds to every interface so a phone on the same Wi-Fi can use the tool.
LAN = "--lan" in sys.argv or os.environ.get("MT_LAN") == "1"
# MT_PUBLIC=1 = hosted on the internet for strangers: no local paths, small uploads, jobs are private
# to the browser that created them (ids kept in its localStorage) and are deleted after JOB_TTL_HOURS.
PUBLIC = os.environ.get("MT_PUBLIC") == "1"
HOST = "0.0.0.0" if (LAN or PUBLIC) else "127.0.0.1"
MAX_UPLOAD_MB = 60 if PUBLIC else 2048
MAX_WORKERS = 4 if PUBLIC else 16
JOB_TTL_HOURS = float(os.environ.get("MT_JOB_TTL_HOURS", 12))
ALLOWED_ROOTS = [Path(p).resolve() for p in os.environ.get("MT_ALLOWED_ROOTS", "").split(";") if p.strip()]
SITE_DIR = ROOT / "site"
APK_PATH = ROOT / "FINAL" / "MultiTranslate.apk"

app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.config["TEMPLATES_AUTO_RELOAD"] = True


def lan_ip() -> str:
    """Best-effort LAN address (no packets are sent; the UDP socket just picks a route)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


@dataclass
class Job:
    id: str
    created: float
    options: JobOptions
    status: JobStatus = field(default_factory=JobStatus)
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    def summary(self) -> dict:
        s = self.status
        return {"id": self.id, "created": self.created, "state": s.state, "targets": self.options.targets,
                "mode": self.options.mode, "input": str(self.options.input), "files_done": s.files_done,
                "files_total": s.files_total, "errors": len(s.errors), "elapsed": s.as_dict()["elapsed"],
                "outputs": len(s.outputs)}

    def to_record(self) -> dict:
        opts = asdict(self.options)
        opts = {k: (str(v) if isinstance(v, Path) else v) for k, v in opts.items()}
        st = self.status.as_dict()
        st["log"] = st["log"][-50:]
        return {"id": self.id, "created": self.created, "options": opts, "status": st}

    @classmethod
    def from_record(cls, rec: dict) -> "Job":
        o = dict(rec["options"])
        for key in ("input", "output", "cache_path", "glossary"):
            if o.get(key):
                o[key] = Path(o[key])
        job = cls(id=rec["id"], created=rec["created"], options=JobOptions(**o))
        st = rec.get("status", {})
        for key in ("state", "files_total", "files_done", "outputs", "errors", "log", "warnings",
                    "started_at", "finished_at", "requests_made", "strings_done", "strings_total"):
            if key in st:
                setattr(job.status, key, st[key])
        if job.status.state == "running":                      # server died mid-job
            job.status.state = "stopped"
        return job


JOBS: dict[str, Job] = {}
_HISTORY_LOCK = threading.Lock()


# ----------------------------------------------------------------------------- history
def save_history() -> None:
    with _HISTORY_LOCK:
        JOBS_DIR.mkdir(exist_ok=True)
        records = [j.to_record() for j in sorted(JOBS.values(), key=lambda j: j.created)]
        HISTORY_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")


def load_history() -> None:
    if not HISTORY_FILE.exists():
        return
    try:
        for rec in json.loads(HISTORY_FILE.read_text(encoding="utf-8")):
            JOBS[rec["id"]] = Job.from_record(rec)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"history.json unreadable, starting fresh: {exc}")


def _run_and_record(job: Job) -> None:
    run_job(job.options, job.status, None, job.stop_event)
    save_history()


def start_job(job: Job) -> None:
    job.stop_event = threading.Event()
    job.status = JobStatus(state="running")            # visible as running before the thread picks it up
    job.thread = threading.Thread(target=_run_and_record, args=(job,), daemon=True)
    job.thread.start()
    save_history()


# ----------------------------------------------------------------------------- security
@app.before_request
def csrf_guard():
    if request.method in ("POST", "DELETE", "PUT", "PATCH"):
        origin = request.headers.get("Origin")
        if request.headers.get("X-Requested-With") != "fetch":
            abort(403, "missing X-Requested-With header")
        if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
            abort(403, "cross-origin request rejected")


def _check_local_path(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        abort(400, f"Path not found: {raw}")
    if ALLOWED_ROOTS and not any(path == r or r in path.parents for r in ALLOWED_ROOTS):
        abort(403, "path is outside MT_ALLOWED_ROOTS")
    return path


def _int(name: str, default: int, upper: int = 1_000_000) -> int:
    try:
        return min(upper, max(1, int(request.form.get(name, default))))
    except ValueError:
        return default


def _safe_relative(name: str) -> Path | None:
    parts = [secure_filename(p) for p in Path(name.replace("\\", "/")).parts if p not in ("", ".", "..")]
    parts = [p for p in parts if p]
    return Path(*parts) if parts else None


def _save_uploads(job_dir: Path) -> Path:
    """Store uploaded files (relative paths from folder uploads are preserved, sanitised)."""
    input_dir = job_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    for f in request.files.getlist("files"):
        rel = _safe_relative(f.filename or "")
        if rel is None:
            continue
        target = input_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        f.save(target)
    return input_dir


def _save_glossary(job_dir: Path) -> Path | None:
    f = request.files.get("glossary")
    if not f or not f.filename:
        return None
    name = secure_filename(f.filename) or "glossary.csv"
    if not name.lower().endswith((".csv", ".json")):
        abort(400, "glossary must be .csv or .json")
    path = job_dir / "glossary" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    f.save(path)
    return path


# ----------------------------------------------------------------------------- routes
def _app_page():
    return render_template("index.html", languages=LANGUAGES, native=NATIVE_NAMES, featured=FEATURED,
                           public=PUBLIC, defaults={"workers": DEFAULT_WORKERS, "rps": DEFAULT_RPS,
                                                    "autosave": DEFAULT_AUTOSAVE})


@app.get("/")
def index():
    """Hosted: the website. Local: straight into the tool."""
    if PUBLIC and (SITE_DIR / "index.html").exists():
        return send_from_directory(SITE_DIR, "index.html")
    return _app_page()


@app.get("/app")
def app_page():
    return _app_page()


@app.get("/site/<path:name>")
def site_file(name: str):
    return send_from_directory(SITE_DIR, name)


@app.get("/<name>.html")
def site_page(name: str):
    """privacy.html, terms.html ... linked relatively from the landing page."""
    return send_from_directory(SITE_DIR, f"{name}.html")


@app.get("/logo.png")
@app.get("/favicon.png")
def site_asset():
    return send_from_directory(SITE_DIR, request.path.lstrip("/"))


@app.get("/download/MultiTranslate.apk")
def download_apk():
    if not APK_PATH.exists():
        abort(404, "APK not built")
    return send_file(APK_PATH, as_attachment=True, download_name="MultiTranslate.apk",
                     mimetype="application/vnd.android.package-archive")


@app.get("/health")
def health():
    return jsonify(ok=True, public=PUBLIC, jobs=len(JOBS))


@app.get("/manifest.json")
def manifest():
    return jsonify({
        "name": "MultiTranslate", "short_name": "Translate", "start_url": "/app" if PUBLIC else "/", "display": "standalone",
        "background_color": "#FBFAF6", "theme_color": "#17203F",
        "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}]})


@app.get("/sw.js")
def service_worker():
    # Minimal worker: makes the page installable ("Add to Home screen"); no offline caching of jobs.
    body = "self.addEventListener('install',e=>self.skipWaiting());self.addEventListener('fetch',()=>{});"
    return app.response_class(body, mimetype="application/javascript")


@app.post("/jobs")
def create_job():
    targets = parse_language_list(request.form.get("targets", ""))
    if not targets:
        return jsonify(error="Choose at least one language"), 400
    job_id = uuid.uuid4().hex[:10]
    job_dir = JOBS_DIR / job_id
    local_path = request.form.get("local_path", "").strip().strip('"')
    if local_path:
        if PUBLIC:
            return jsonify(error="Local paths are not available on the hosted version - upload the files"), 400
        src = _check_local_path(local_path)
    else:
        src = _save_uploads(job_dir)
        if not any(p.is_file() for p in src.rglob("*")):
            shutil.rmtree(job_dir, ignore_errors=True)
            return jsonify(error="No files uploaded"), 400
    mode = request.form.get("mode", "separate")
    sheet_mode = request.form.get("sheet_mode", "columns")
    if mode not in ("separate", "combined") or sheet_mode not in ("columns", "sheets"):
        return jsonify(error="invalid mode"), 400
    try:
        rps = min(10.0, max(0.2, float(request.form.get("rps", DEFAULT_RPS) or DEFAULT_RPS)))
    except ValueError:
        rps = DEFAULT_RPS
    opts = JobOptions(
        input=src, output=job_dir / "output", targets=targets, mode=mode, sheet_mode=sheet_mode,
        source=(request.form.get("source", "auto").strip() or "auto")[:10],
        workers=_int("workers", DEFAULT_WORKERS, MAX_WORKERS), autosave_every=_int("autosave", DEFAULT_AUTOSAVE),
        requests_per_second=rps, protect=request.form.get("protect", "1") != "0",
        hinglish=request.form.get("hinglish", "1") != "0", glossary=_save_glossary(job_dir))
    job = Job(id=job_id, created=time.time(), options=opts)
    JOBS[job_id] = job
    start_job(job)
    return jsonify(id=job_id)


@app.get("/jobs")
def list_jobs():
    """Hosted: only the jobs whose ids the browser presents (?ids=a,b,c) - strangers never see each other."""
    jobs = JOBS.values()
    if PUBLIC:
        wanted = set(request.args.get("ids", "").split(","))
        jobs = [j for j in jobs if j.id in wanted]
    return jsonify([j.summary() for j in sorted(jobs, key=lambda j: -j.created)])


def cleanup_expired() -> int:
    """Delete finished jobs older than JOB_TTL_HOURS (uploads, outputs, history entry)."""
    cutoff = time.time() - JOB_TTL_HOURS * 3600
    expired = [j for j in list(JOBS.values()) if j.created < cutoff and not (j.thread and j.thread.is_alive())]
    for job in expired:
        JOBS.pop(job.id, None)
        shutil.rmtree(JOBS_DIR / job.id, ignore_errors=True)
    if expired:
        save_history()
    return len(expired)


def _cleanup_loop() -> None:
    while True:
        time.sleep(600)
        try:
            cleanup_expired()
        except Exception as exc:                       # never let housekeeping kill the server
            print(f"cleanup failed: {exc}")


@app.get("/jobs/<job_id>")
def job_status(job_id: str):
    job = JOBS.get(job_id) or abort(404)
    data = job.status.as_dict()
    out = job.options.output
    data["outputs"] = [Path(p).relative_to(out).as_posix() for p in job.status.outputs if Path(p).exists()]
    data["output_dir"] = str(out)
    data["options"] = {"targets": job.options.targets, "mode": job.options.mode, "input": str(job.options.input)}
    return jsonify(data)


@app.post("/jobs/<job_id>/stop")
def stop_job(job_id: str):
    job = JOBS.get(job_id) or abort(404)
    job.stop_event.set()
    return jsonify(ok=True)


@app.post("/jobs/<job_id>/resume")
def resume_job(job_id: str):
    """Re-run with the same options: finished outputs are skipped, cached strings cost no requests."""
    job = JOBS.get(job_id) or abort(404)
    if job.thread and job.thread.is_alive():
        return jsonify(error="job is still running"), 409
    if not job.options.input.exists():
        return jsonify(error="input no longer exists"), 410
    start_job(job)
    return jsonify(ok=True)


@app.get("/jobs/<job_id>/download")
def download_zip(job_id: str):
    job = JOBS.get(job_id) or abort(404)
    out = job.options.output
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(out.rglob("*")) if out.exists() else []:
            if p.is_file() and p.name != ".progress.json":
                zf.write(p, p.relative_to(out).as_posix())
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"translated_{job_id}.zip", mimetype="application/zip")


@app.get("/jobs/<job_id>/files/<path:rel>")
def download_file(job_id: str, rel: str):
    job = JOBS.get(job_id) or abort(404)
    root = job.options.output.resolve()
    path = (root / rel).resolve()
    if root not in path.parents or not path.is_file() or path.name == ".progress.json":
        abort(404)
    return send_file(path, as_attachment=True)


@app.delete("/jobs/<job_id>")
def delete_job(job_id: str):
    job = JOBS.pop(job_id, None) or abort(404)
    job.stop_event.set()
    if job.thread and job.thread.is_alive():
        job.thread.join(timeout=10)
    shutil.rmtree(JOBS_DIR / job_id, ignore_errors=True)
    save_history()
    return jsonify(ok=True)


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(409)
@app.errorhandler(413)
def _json_error(err):
    return jsonify(error=getattr(err, "description", str(err))), err.code


load_history()
if PUBLIC:
    cleanup_expired()
    threading.Thread(target=_cleanup_loop, daemon=True).start()

if __name__ == "__main__":
    JOBS_DIR.mkdir(exist_ok=True)
    print(f"MultiTranslate Web UI -> http://127.0.0.1:{PORT}" + (f"   (phone: http://{lan_ip()}:{PORT})" if LAN else ""))
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
