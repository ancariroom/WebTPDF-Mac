#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import uvicorn


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "20_Backend"
NODE_ENTRYPOINT = BACKEND_DIR / "bin" / "ebook-convert.js"
PY_WRAPPER = BACKEND_DIR / "scripts" / "run_ebook2audiobook.py"
OUTPUT_ROOT = REPO_ROOT / "40_Ops" / "output" / "audiobooks"
JOB_ROOT = OUTPUT_ROOT / "_ui_jobs"
DEFAULT_IMAGE = "athomasson2/ebook2audiobook:cpu"
DEFAULT_LANGUAGE = "jpn"
DEFAULT_OUTPUT_FORMAT = "mp3"
AUDIO_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".m4b",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
}
DOCKER_CANDIDATES = (
    Path("/usr/local/bin/docker"),
    Path("/opt/homebrew/bin/docker"),
    Path("/Applications/Docker.app/Contents/Resources/bin/docker"),
)


app = FastAPI(title="WebTPDF Audio Lab")
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sanitize_filename(name: str | None, fallback: str) -> str:
    candidate = Path(name or fallback).name
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip("._")
    return candidate or fallback


def detect_docker() -> str | None:
    found = shutil.which("docker")
    if found:
        return found
    for candidate in DOCKER_CANDIDATES:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def detect_node() -> str | None:
    return shutil.which("node")


def detect_python() -> str | None:
    return shutil.which("python3") or shutil.which("python")


def detect_launcher() -> str:
    node = detect_node()
    if node and NODE_ENTRYPOINT.is_file():
        return shlex.join([node, str(NODE_ENTRYPOINT), "audiobook"])

    python = detect_python()
    if python and PY_WRAPPER.is_file():
        return shlex.join([python, str(PY_WRAPPER)])

    return "unavailable"


def ensure_job_dir(job_id: str) -> tuple[Path, Path, Path]:
    job_dir = JOB_ROOT / job_id
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    return job_dir, input_dir, output_dir


def save_upload(upload: UploadFile, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    upload.file.seek(0)
    with target.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)
    return target


def entrypoint_command() -> list[str]:
    node = detect_node()
    if node and NODE_ENTRYPOINT.is_file():
        return [node, str(NODE_ENTRYPOINT), "audiobook"]

    python = detect_python()
    if python and PY_WRAPPER.is_file():
        return [python, str(PY_WRAPPER)]

    raise HTTPException(status_code=500, detail="Neither Node nor Python wrapper entrypoint was found.")


def build_command(
    *,
    ebook_path: Path,
    output_dir: Path,
    image: str,
    language: str,
    output_format: str,
    tts_engine: str | None,
    device: str | None,
    voice_path: Path | None,
    custom_model_path: Path | None,
    dry_run: bool,
) -> list[str]:
    command = entrypoint_command()

    if command[1] == str(NODE_ENTRYPOINT):
        command += [
            "--ebook",
            str(ebook_path),
            "--language",
            language,
            "--output-dir",
            str(output_dir),
            "--output-format",
            output_format,
        ]
        if tts_engine:
            command += ["--tts-engine", tts_engine]
        if device:
            command += ["--device", device]
        if image:
            command += ["--image", image]
        if voice_path:
            command += ["--voice", str(voice_path)]
        if custom_model_path:
            command += ["--custom-model", str(custom_model_path)]
        if dry_run:
            command += ["--dry-run"]
        return command

    command += [
        "--ebook",
        str(ebook_path),
        "--language",
        language,
        "--output-dir",
        str(output_dir),
        "--output-format",
        output_format,
    ]
    if tts_engine:
        command += ["--tts-engine", tts_engine]
    if device:
        command += ["--device", device]
    if image:
        command += ["--image", image]
    if voice_path:
        command += ["--voice", str(voice_path)]
    if custom_model_path:
        command += ["--custom-model", str(custom_model_path)]
    if dry_run:
        command += ["--dry-run"]
    return command


def append_job_log(job_id: str, line: str) -> None:
    with _jobs_lock:
        job = _jobs[job_id]
        logs = job.setdefault("logs", [])
        logs.append(line)
        if len(logs) > 500:
            del logs[:-500]


def update_job(job_id: str, **updates: Any) -> None:
    with _jobs_lock:
        _jobs[job_id].update(updates)


def snapshot_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = dict(_jobs[job_id])
        job["logs"] = list(job.get("logs", []))
        return job


def find_audio_artifact(output_dir: Path) -> Path | None:
    candidates: list[Path] = []
    for path in output_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
            candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def run_job(job_id: str) -> None:
    job = snapshot_job(job_id)
    update_job(
        job_id,
        status="running",
        message="変換中",
        started_at=now_iso(),
    )

    command = job["command_list"]
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("NODE_DISABLE_COLORS", "1")

    try:
        process = subprocess.Popen(
            command,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
    except Exception as exc:
        update_job(
            job_id,
            status="error",
            message="プロセス起動に失敗",
            error=str(exc),
            finished_at=now_iso(),
        )
        append_job_log(job_id, f"[launcher] {exc}")
        return

    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip("\n")
        if line:
            append_job_log(job_id, line)

    returncode = process.wait()
    output_dir = Path(job["output_dir"])
    artifact_path = find_audio_artifact(output_dir) if returncode == 0 and not job["dry_run"] else None

    if job["dry_run"] and returncode == 0:
        update_job(
            job_id,
            status="completed",
            message="dry-run 完了",
            returncode=returncode,
            finished_at=now_iso(),
            artifact_path=None,
        )
        return

    if returncode != 0:
        update_job(
            job_id,
            status="error",
            message=f"変換失敗 (exit {returncode})",
            returncode=returncode,
            finished_at=now_iso(),
            artifact_path=None,
        )
        return

    if artifact_path is None:
        update_job(
            job_id,
            status="error",
            message="変換は完了したが音声ファイルが見つからない",
            returncode=returncode,
            finished_at=now_iso(),
            artifact_path=None,
        )
        return

    update_job(
        job_id,
        status="completed",
        message=f"{artifact_path.name} を生成",
        returncode=returncode,
        finished_at=now_iso(),
        artifact_path=str(artifact_path),
    )


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    page = INDEX_HTML
    page = page.replace("__DEFAULT_IMAGE__", html.escape(DEFAULT_IMAGE))
    page = page.replace("__DEFAULT_LANGUAGE__", html.escape(DEFAULT_LANGUAGE))
    page = page.replace("__DEFAULT_OUTPUT_FORMAT__", html.escape(DEFAULT_OUTPUT_FORMAT))
    return HTMLResponse(page)


@app.get("/api/system")
def system_info() -> JSONResponse:
    return JSONResponse(
        {
            "repo_root": str(REPO_ROOT),
            "output_root": str(OUTPUT_ROOT),
            "launcher": detect_launcher(),
            "node": detect_node(),
            "python": detect_python(),
            "docker": detect_docker(),
            "default_image": DEFAULT_IMAGE,
        }
    )


@app.post("/api/jobs")
async def create_job(
    ebook: UploadFile = File(...),
    voice: UploadFile | None = File(None),
    custom_model: UploadFile | None = File(None),
    language: str = Form(DEFAULT_LANGUAGE),
    output_format: str = Form(DEFAULT_OUTPUT_FORMAT),
    tts_engine: str = Form(""),
    device: str = Form(""),
    image: str = Form(DEFAULT_IMAGE),
    dry_run: bool = Form(False),
) -> JSONResponse:
    job_id = uuid.uuid4().hex[:12]
    _, input_dir, output_dir = ensure_job_dir(job_id)

    ebook_name = sanitize_filename(ebook.filename, "ebook.epub")
    ebook_path = save_upload(ebook, input_dir / ebook_name)
    voice_path = None
    custom_model_path = None

    if voice and voice.filename:
        voice_name = sanitize_filename(voice.filename, "voice_sample.wav")
        voice_path = save_upload(voice, input_dir / voice_name)

    if custom_model and custom_model.filename:
        model_name = sanitize_filename(custom_model.filename, "custom_model.zip")
        custom_model_path = save_upload(custom_model, input_dir / model_name)

    command = build_command(
        ebook_path=ebook_path,
        output_dir=output_dir,
        image=image.strip(),
        language=language.strip() or DEFAULT_LANGUAGE,
        output_format=output_format.strip() or DEFAULT_OUTPUT_FORMAT,
        tts_engine=tts_engine.strip() or None,
        device=device.strip() or None,
        voice_path=voice_path,
        custom_model_path=custom_model_path,
        dry_run=dry_run,
    )

    job_record = {
        "job_id": job_id,
        "status": "queued",
        "message": "待機中",
        "created_at": now_iso(),
        "started_at": None,
        "finished_at": None,
        "returncode": None,
        "dry_run": dry_run,
        "language": language.strip() or DEFAULT_LANGUAGE,
        "output_format": output_format.strip() or DEFAULT_OUTPUT_FORMAT,
        "tts_engine": tts_engine.strip() or None,
        "device": device.strip() or None,
        "image": image.strip() or DEFAULT_IMAGE,
        "ebook_name": ebook_name,
        "output_dir": str(output_dir),
        "command": shlex.join(command),
        "command_list": command,
        "artifact_path": None,
        "logs": [],
    }

    with _jobs_lock:
        _jobs[job_id] = job_record

    threading.Thread(target=run_job, args=(job_id,), daemon=True).start()
    return JSONResponse({"job_id": job_id, "status": "queued"})


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> JSONResponse:
    with _jobs_lock:
        if job_id not in _jobs:
            raise HTTPException(status_code=404, detail="Job not found")

    job = snapshot_job(job_id)
    artifact_path = job.get("artifact_path")
    artifact_url = f"/api/jobs/{job_id}/artifact" if artifact_path else None

    return JSONResponse(
        {
            "job_id": job_id,
            "status": job["status"],
            "message": job["message"],
            "created_at": job["created_at"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "returncode": job["returncode"],
            "dry_run": job["dry_run"],
            "language": job["language"],
            "output_format": job["output_format"],
            "tts_engine": job["tts_engine"],
            "device": job["device"],
            "image": job["image"],
            "ebook_name": job["ebook_name"],
            "output_dir": job["output_dir"],
            "command": job["command"],
            "artifact_path": artifact_path,
            "artifact_url": artifact_url,
            "logs": "\n".join(job["logs"]),
        }
    )


@app.get("/api/jobs/{job_id}/artifact")
def get_artifact(job_id: str):
    with _jobs_lock:
        if job_id not in _jobs:
            raise HTTPException(status_code=404, detail="Job not found")
        artifact_path = _jobs[job_id].get("artifact_path")

    if not artifact_path:
        raise HTTPException(status_code=404, detail="Artifact not ready")

    path = Path(artifact_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact file missing")

    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


INDEX_HTML = """<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WebTPDF Audio Lab</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f4;
      --panel: #ffffff;
      --panel-alt: #eef2f1;
      --text: #132026;
      --muted: #5f6c72;
      --line: #d8dfdc;
      --accent: #0f766e;
      --accent-strong: #115e59;
      --danger: #b91c1c;
      --shadow: 0 12px 30px rgba(17, 24, 39, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      width: 100%;
      overflow-x: hidden;
      background:
        linear-gradient(180deg, rgba(15, 118, 110, 0.05), transparent 24%),
        var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }
    .shell {
      max-width: 1240px;
      width: min(1240px, 100%);
      margin: 0 auto;
      padding: 24px;
    }
    .hero {
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      align-items: end;
      justify-content: space-between;
      margin-bottom: 20px;
      min-width: 0;
    }
    .hero > div {
      min-width: 0;
    }
    .hero h1 {
      margin: 0;
      font-size: 28px;
      letter-spacing: 0;
    }
    .hero p {
      margin: 6px 0 0;
      color: var(--muted);
      max-width: 70ch;
    }
    .status-line {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      background: var(--panel);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      font-size: 14px;
      color: var(--muted);
      max-width: 100%;
    }
    #system-text {
      overflow-wrap: anywhere;
    }
    .status-dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: var(--muted);
    }
    .status-dot.ok { background: #16a34a; }
    .status-dot.warn { background: #f59e0b; }
    .status-dot.err { background: var(--danger); }
    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr);
      gap: 16px;
      align-items: start;
      min-width: 0;
    }
    @media (max-width: 960px) {
      .grid { grid-template-columns: 1fr; }
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: var(--shadow);
      padding: 18px;
      min-width: 0;
      width: 100%;
    }
    .panel h2 {
      margin: 0 0 14px;
      font-size: 18px;
    }
    .note {
      color: var(--muted);
      font-size: 14px;
      margin-top: 8px;
      overflow-wrap: anywhere;
    }
    .note code {
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    .form-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      min-width: 0;
    }
    @media (max-width: 720px) {
      .form-grid { grid-template-columns: 1fr; }
    }
    label {
      display: grid;
      gap: 6px;
      font-size: 14px;
      color: var(--muted);
      min-width: 0;
    }
    input, select, button {
      font: inherit;
      min-width: 0;
    }
    input[type="text"],
    input[type="url"],
    input[type="file"],
    select {
      width: 100%;
      max-width: 100%;
      padding: 11px 12px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
      color: var(--text);
    }
    input[type="file"] {
      display: block;
    }
    input[type="checkbox"] {
      width: 18px;
      height: 18px;
      margin: 0;
    }
    .inline {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .buttons {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 14px;
      min-width: 0;
    }
    button {
      appearance: none;
      border: 0;
      border-radius: 10px;
      padding: 11px 16px;
      cursor: pointer;
      font-weight: 650;
    }
    .primary {
      background: var(--accent);
      color: white;
    }
    .primary:hover {
      background: var(--accent-strong);
    }
    .ghost {
      background: var(--panel-alt);
      color: var(--text);
    }
    .stack {
      display: grid;
      gap: 12px;
      min-width: 0;
    }
    .keyline {
      display: grid;
      gap: 4px;
      padding: 12px;
      border-radius: 10px;
      background: var(--panel-alt);
      border: 1px solid var(--line);
      min-width: 0;
    }
    .keyline strong {
      font-size: 13px;
    }
    .keyline span {
      color: var(--muted);
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    pre {
      margin: 0;
      padding: 14px;
      border-radius: 10px;
      background: #0b1220;
      color: #d6e7ff;
      overflow: auto;
      max-height: 340px;
      font-size: 13px;
      line-height: 1.5;
      white-space: pre-wrap;
      word-break: break-word;
      min-width: 0;
    }
    .row {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      min-width: 0;
    }
    .artifact {
      display: grid;
      gap: 8px;
      margin-top: 12px;
      padding-top: 12px;
      border-top: 1px solid var(--line);
      min-width: 0;
    }
    .artifact[hidden] {
      display: none !important;
    }
    audio {
      width: 100%;
    }
    code {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
    }
    .tiny {
      font-size: 12px;
      color: var(--muted);
    }
    a {
      color: var(--accent);
    }
  </style>
</head>
<body>
  <main class="shell">
    <div class="hero">
      <div>
        <h1>WebTPDF Audio Lab</h1>
        <p>PDF / EPUB / MOBI をアップロードして、`ebook2audiobook` がどんな音声ファイルを返すかその場で試すための画面です。出力は job ごとに分けて保存します。</p>
      </div>
      <div class="status-line" id="system-status">
        <span class="status-dot warn" id="system-dot"></span>
        <span id="system-text">system check...</span>
      </div>
    </div>

    <div class="grid">
      <section class="panel">
        <h2>入力</h2>
        <form id="job-form" class="stack">
          <label>
            電子書籍ファイル
            <input name="ebook" type="file" accept=".pdf,.epub,.mobi,.azw3" required>
          </label>

          <div class="form-grid">
            <label>
              言語
              <input name="language" type="text" value="__DEFAULT_LANGUAGE__" placeholder="jpn">
            </label>
            <label>
              出力形式
              <select name="output_format">
                <option value="mp3" selected>mp3</option>
                <option value="m4b">m4b</option>
              </select>
            </label>
            <label>
              TTS エンジン
              <input name="tts_engine" type="text" placeholder="任意">
            </label>
            <label>
              デバイス
              <select name="device">
                <option value="">auto / CPU</option>
                <option value="CPU">CPU</option>
                <option value="CUDA">CUDA</option>
                <option value="ROCM">ROCM</option>
                <option value="XPU">XPU</option>
                <option value="JETSON">JETSON</option>
              </select>
            </label>
            <label>
              Docker イメージ
              <input name="image" type="text" value="__DEFAULT_IMAGE__">
            </label>
            <label>
              音声サンプル
              <input name="voice" type="file" accept=".wav,.mp3,.m4a,.flac,.ogg">
            </label>
            <label>
              カスタムモデル
              <input name="custom_model" type="file" accept=".zip">
            </label>
            <label class="inline" style="align-self:end;">
              <input name="dry_run" type="checkbox">
              <span>dry-run</span>
            </label>
          </div>

          <div class="buttons">
            <button class="primary" type="submit">変換を開始</button>
            <button class="ghost" type="button" id="reset-btn">フォームをクリア</button>
          </div>

          <div class="note">
            出力先は `40_Ops/output/audiobooks/_ui_jobs/&lt;job_id&gt;/output/` に切り分けます。ブラウザで再生したいときは `mp3` を選ぶのが無難です。
          </div>
        </form>
      </section>

      <section class="panel">
        <h2>結果</h2>
        <div class="stack">
          <div class="keyline">
            <strong>Status</strong>
            <span id="status-text">idle</span>
          </div>
          <div class="keyline">
            <strong>Job</strong>
            <span id="job-id">-</span>
          </div>
          <div class="keyline">
            <strong>Command</strong>
            <span id="command-text">-</span>
          </div>
          <div class="keyline">
            <strong>Artifact</strong>
            <span id="artifact-text">-</span>
          </div>
          <div class="artifact" id="artifact-block" hidden>
            <audio id="audio-player" controls></audio>
            <a id="download-link" href="#" download>download</a>
          </div>
          <pre id="log-box">Waiting for a job...</pre>
        </div>
      </section>
    </div>
  </main>

  <script>
    const form = document.getElementById('job-form');
    const resetBtn = document.getElementById('reset-btn');
    const statusText = document.getElementById('status-text');
    const jobIdText = document.getElementById('job-id');
    const commandText = document.getElementById('command-text');
    const artifactText = document.getElementById('artifact-text');
    const logBox = document.getElementById('log-box');
    const artifactBlock = document.getElementById('artifact-block');
    const audioPlayer = document.getElementById('audio-player');
    const downloadLink = document.getElementById('download-link');
    const systemText = document.getElementById('system-text');
    const systemDot = document.getElementById('system-dot');

    async function loadSystem() {
      try {
        const response = await fetch('/api/system');
        const data = await response.json();
        const docker = data.docker ? 'Docker ready' : 'Docker missing';
        systemText.textContent = docker + ' · launcher: ' + data.launcher;
        systemDot.className = 'status-dot ' + (data.docker ? 'ok' : 'err');
      } catch (error) {
        systemText.textContent = 'system check failed';
        systemDot.className = 'status-dot err';
      }
    }

    function renderJob(data) {
      statusText.textContent = data.status + ' · ' + data.message;
      jobIdText.textContent = data.job_id;
      commandText.textContent = data.command;
      artifactText.textContent = data.artifact_path || '-';
      logBox.textContent = data.logs || 'No logs yet.';

      if (data.artifact_url) {
        artifactBlock.hidden = false;
        audioPlayer.src = data.artifact_url + '?t=' + Date.now();
        downloadLink.href = data.artifact_url;
      } else {
        artifactBlock.hidden = true;
        audioPlayer.removeAttribute('src');
        downloadLink.removeAttribute('href');
      }
    }

    async function pollJob(jobId) {
      const response = await fetch('/api/jobs/' + jobId);
      if (!response.ok) {
        statusText.textContent = 'job lookup failed';
        return;
      }
      const data = await response.json();
      renderJob(data);
      if (data.status === 'queued' || data.status === 'running') {
        setTimeout(() => pollJob(jobId), 1500);
      }
    }

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const formData = new FormData(form);
      statusText.textContent = 'starting...';
      commandText.textContent = '-';
      artifactText.textContent = '-';
      logBox.textContent = 'Submitting job...';

      const response = await fetch('/api/jobs', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const errorText = await response.text();
        statusText.textContent = 'submit failed';
        logBox.textContent = errorText;
        return;
      }

      const data = await response.json();
      jobIdText.textContent = data.job_id;
      pollJob(data.job_id);
    });

    resetBtn.addEventListener('click', () => {
      form.reset();
      document.querySelector('input[name="language"]').value = '__DEFAULT_LANGUAGE__';
      document.querySelector('input[name="image"]').value = '__DEFAULT_IMAGE__';
      document.querySelector('select[name="output_format"]').value = '__DEFAULT_OUTPUT_FORMAT__';
    });

    loadSystem();
  </script>
</body>
</html>
"""


def cli() -> None:
    parser = argparse.ArgumentParser(description="Launch the WebTPDF audiobook UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765).")
    args = parser.parse_args()

    JOB_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"Serving WebTPDF Audio Lab on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    cli()
