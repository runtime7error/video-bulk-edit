"""Auto Cutter - servidor web.

Envie videos pelo navegador (celular ou PC); cada video e espelhado, cortado
em trechos de N segundos e reunido com crossfade. Os jobs ficam numa fila e
sao processados um de cada vez (video pesa muito em CPU/RAM).

Variaveis de ambiente:
  APP_PASSWORD   se definida, exige login (usuario qualquer + essa senha)
  DATA_DIR       pasta de trabalho (padrao: /tmp/autocutter)
  JOB_TTL_MIN    minutos ate apagar os arquivos de um job (padrao: 60)
  MAX_UPLOAD_MB  tamanho maximo por arquivo (padrao: 500)
  X264_PRESET    padrao: veryfast  (ultrafast = mais rapido/menos RAM)
  X264_CRF       padrao: 20
  FFMPEG_THREADS padrao: 0 (auto). Use 1-2 em planos com pouca RAM
"""
from __future__ import annotations

import os
import queue
import re
import secrets
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from . import processor

DATA_DIR = Path(os.environ.get("DATA_DIR", "/tmp/autocutter"))
JOB_TTL = int(os.environ.get("JOB_TTL_MIN", "60")) * 60
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB", "500")) * 1024 * 1024
PRESET = os.environ.get("X264_PRESET", "veryfast")
CRF = int(os.environ.get("X264_CRF", "20"))
THREADS = int(os.environ.get("FFMPEG_THREADS", "0"))
PASSWORD = os.environ.get("APP_PASSWORD", "")
TRANSITIONS = {"fade", "dissolve", "fadeblack", "fadewhite", "smoothleft", "smoothright",
               "wipeleft", "wiperight", "slideleft", "slideright", "circleopen", "radial"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".3gp"}

DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Auto Cutter")
security = HTTPBasic(auto_error=False)


def auth(creds: Optional[HTTPBasicCredentials] = Depends(security)):
    if not PASSWORD:
        return
    if not creds or not secrets.compare_digest(creds.password.encode(), PASSWORD.encode()):
        raise HTTPException(401, "Senha incorreta", headers={"WWW-Authenticate": 'Basic realm="Auto Cutter"'})


# ---------------- Jobs ----------------
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
work_queue: "queue.Queue[str]" = queue.Queue()


def _safe_name(name: str) -> str:
    base = Path(name or "video").name
    base = re.sub(r"[^\w\-. ]+", "_", base, flags=re.UNICODE).strip() or "video"
    return base[:120]


def _public(job: dict) -> dict:
    position = 0
    if job["status"] == "queued":
        with work_queue.mutex:
            pending = list(work_queue.queue)
        position = pending.index(job["id"]) + 1 if job["id"] in pending else 0
    return {
        "id": job["id"],
        "status": job["status"],
        "queue_position": position,
        "created": job["created"],
        "expires_in": max(0, int(job["created"] + JOB_TTL - time.time())),
        "settings": job["settings"],
        "files": [{k: f[k] for k in ("name", "status", "progress", "error", "output_name", "info")}
                  for f in job["files"]],
    }


def worker():
    while True:
        job_id = work_queue.get()
        job = jobs.get(job_id)
        if not job:
            continue
        job["status"] = "processing"
        s = job["settings"]
        for f in job["files"]:
            if f["status"] != "queued":
                continue
            f["status"] = "processing"

            def prog(p: float, f=f):
                f["progress"] = round(p, 4)

            try:
                f["info"] = processor.process_video(
                    f["src"], f["dst"], segment=s["segmento"], fade=s["fade"],
                    transition=s["transicao"], crf=CRF, preset=PRESET, threads=THREADS,
                    on_progress=prog,
                )
                f["status"] = "done"
            except Exception as e:  # noqa: BLE001
                f["status"] = "error"
                f["error"] = str(e)[:500]
            finally:
                try:
                    os.remove(f["src"])  # libera disco
                except OSError:
                    pass
        job["status"] = "done"


def janitor():
    while True:
        time.sleep(60)
        now = time.time()
        for job_id, job in list(jobs.items()):
            if job["status"] in ("done",) and now - job["created"] > JOB_TTL:
                shutil.rmtree(job["dir"], ignore_errors=True)
                with jobs_lock:
                    jobs.pop(job_id, None)


threading.Thread(target=worker, daemon=True).start()
threading.Thread(target=janitor, daemon=True).start()


# ---------------- API ----------------
@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/api/jobs", dependencies=[Depends(auth)])
async def create_job(
    files: list[UploadFile] = File(...),
    segmento: float = Form(5.0),
    fade: float = Form(0.5),
    transicao: str = Form("fade"),
):
    if not (1 <= segmento <= 60):
        raise HTTPException(400, "segmento deve estar entre 1 e 60 s")
    if not (0.1 <= fade < segmento):
        raise HTTPException(400, "fade deve ser >= 0.1 e menor que o segmento")
    if transicao not in TRANSITIONS:
        raise HTTPException(400, "transicao invalida")

    job_id = uuid.uuid4().hex[:12]
    job_dir = DATA_DIR / job_id
    (job_dir / "in").mkdir(parents=True)
    (job_dir / "out").mkdir()

    entries = []
    used: set[str] = set()
    try:
        for i, up in enumerate(files):
            name = _safe_name(up.filename or f"video{i}.mp4")
            ext = Path(name).suffix.lower()
            if ext not in VIDEO_EXT:
                raise HTTPException(400, f"formato nao suportado: {name}")
            stem = Path(name).stem
            out_name = f"{stem}_editado.mp4"
            n = 2
            while out_name in used:
                out_name = f"{stem}_editado_{n}.mp4"; n += 1
            used.add(out_name)

            src = job_dir / "in" / f"{i}{ext}"
            size = 0
            with open(src, "wb") as fh:
                while chunk := await up.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, f"{name} passa de {MAX_UPLOAD // 1024 // 1024} MB")
                    fh.write(chunk)
            entries.append({"name": name, "src": str(src), "dst": str(job_dir / "out" / out_name),
                            "output_name": out_name, "status": "queued", "progress": 0.0,
                            "error": None, "info": None})
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    if not entries:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(400, "nenhum video enviado")

    job = {"id": job_id, "dir": str(job_dir), "status": "queued", "created": time.time(),
           "settings": {"segmento": segmento, "fade": fade, "transicao": transicao},
           "files": entries}
    with jobs_lock:
        jobs[job_id] = job
    work_queue.put(job_id)
    return _public(job)


def _get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job nao encontrado (pode ter expirado)")
    return job


@app.get("/api/jobs/{job_id}", dependencies=[Depends(auth)])
def job_status(job_id: str):
    return _public(_get_job(job_id))


@app.get("/api/jobs/{job_id}/files/{idx}", dependencies=[Depends(auth)])
def download_file(job_id: str, idx: int):
    job = _get_job(job_id)
    if not (0 <= idx < len(job["files"])):
        raise HTTPException(404, "arquivo nao encontrado")
    f = job["files"][idx]
    if f["status"] != "done":
        raise HTTPException(409, "arquivo ainda nao esta pronto")
    return FileResponse(f["dst"], media_type="video/mp4", filename=f["output_name"])


@app.get("/api/jobs/{job_id}/zip", dependencies=[Depends(auth)])
def download_zip(job_id: str):
    job = _get_job(job_id)
    done = [f for f in job["files"] if f["status"] == "done"]
    if not done:
        raise HTTPException(409, "nenhum arquivo pronto")
    zip_path = Path(job["dir"]) / f"autocutter_{job_id}_{len(done)}.zip"
    if not zip_path.exists():
        tmp = zip_path.with_suffix(".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:  # mp4 ja e comprimido
            for f in done:
                z.write(f["dst"], f["output_name"])
        tmp.rename(zip_path)
    return FileResponse(zip_path, media_type="application/zip", filename=f"autocutter_{job_id}.zip")


@app.get("/api/config", dependencies=[Depends(auth)])
def config():
    return {"transitions": sorted(TRANSITIONS), "max_upload_mb": MAX_UPLOAD // 1024 // 1024,
            "ttl_min": JOB_TTL // 60}


# A pagina tambem fica protegida pela senha
@app.get("/", dependencies=[Depends(auth)])
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
