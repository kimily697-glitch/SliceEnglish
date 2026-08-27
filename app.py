"""SliceEnglish MVP: temporary chunk upload, English transcription, and sentence practice.

Run with: python app.py
Requires ffmpeg/ffprobe on PATH.  The first transcription downloads the selected
faster-whisper model unless it is already available in the local model cache.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import BadRequest

from segmenter import words_to_segments

APP_DIR = Path(__file__).resolve().parent
WORK_DIR = Path(os.environ.get("SLICEENGLISH_WORK_DIR", APP_DIR / ".sessions"))
WORK_DIR.mkdir(parents=True, exist_ok=True)
MAX_BYTES = 500 * 1024 * 1024
MAX_DURATION_MS = 30 * 60 * 1000
CHUNK_SIZE = 8 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".m4a"}
SESSION_TTL_SECONDS = 60 * 60
MODEL_NAME = os.environ.get("WHISPER_MODEL", "base.en")

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = CHUNK_SIZE + 1024 * 1024
_lock = threading.RLock()
_jobs: dict[str, dict[str, Any]] = {}


def api_error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def session_dir(session_id: str) -> Path:
    return WORK_DIR / session_id


def read_meta(session_id: str) -> dict[str, Any]:
    path = session_dir(session_id) / "meta.json"
    if not path.exists():
        raise BadRequest("上传会话不存在或已过期")
    return json.loads(path.read_text(encoding="utf-8"))


def write_meta(session_id: str, meta: dict[str, Any]) -> None:
    meta["updatedAt"] = int(time.time())
    (session_dir(session_id) / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("未找到 FFmpeg/ffprobe。请安装 FFmpeg 并加入 PATH 后重试。")


def audio_duration_ms(path: Path) -> int:
    require_ffmpeg()
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return round(float(result.stdout.strip()) * 1000)


def normalize_audio(source: Path, destination: Path) -> None:
    require_ffmpeg()
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(destination)],
        check=True, capture_output=True, text=True,
    )


def transcribe(session_id: str) -> None:
    with _lock:
        job = _jobs[session_id]
        job.update(status="processing", progress=8, message="正在标准化音频…")
    directory = session_dir(session_id)
    try:
        meta = read_meta(session_id)
        original = directory / "upload" / meta["filename"]
        normalized = directory / "normalized.wav"
        normalize_audio(original, normalized)
        with _lock:
            _jobs[session_id].update(progress=25, message="正在识别英文语音…")

        from faster_whisper import WhisperModel
        model = WhisperModel(MODEL_NAME, device="auto", compute_type="int8")
        raw_segments, _ = model.transcribe(
            str(normalized), language="en", word_timestamps=True, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        words: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_segments):
            for word in raw.words or []:
                words.append({"word": word.word, "start": word.start, "end": word.end, "probability": word.probability})
            with _lock:
                _jobs[session_id].update(progress=min(90, 35 + index * 3), message="正在按自然停顿切分句子…")
        duration = audio_duration_ms(original)
        if duration > MAX_DURATION_MS:
            raise RuntimeError("音频超过 30 分钟，无法开始练习。")
        segments = words_to_segments(words, duration)
        if not segments:
            raise RuntimeError("未识别到可练习的英文语句，请检查音频语言和清晰度。")
        meta.update(duration=duration, segments=[asdict(s) for s in segments], status="ready")
        write_meta(session_id, meta)
        with _lock:
            _jobs[session_id].update(status="ready", progress=100, message="切分完成，等待你确认。")
    except Exception as exc:  # Do not expose subprocess paths or traces.
        with _lock:
            _jobs[session_id].update(status="failed", progress=0, message=str(exc))


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.post("/api/uploads/init")
def init_upload():
    data = request.get_json(silent=True) or {}
    filename = Path(str(data.get("filename", ""))).name
    size = data.get("size")
    if not filename or Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
        return api_error("仅支持 MP3、WAV、M4A 文件。")
    if not isinstance(size, int) or size <= 0 or size > MAX_BYTES:
        return api_error("文件必须大于 0 且不超过 500MB。")
    session_id = str(uuid.uuid4())
    directory = session_dir(session_id)
    (directory / "parts").mkdir(parents=True)
    meta = {"filename": filename, "size": size, "createdAt": int(time.time()), "updatedAt": int(time.time()), "status": "uploading", "segments": []}
    write_meta(session_id, meta)
    return jsonify({"uploadId": session_id, "chunkSize": CHUNK_SIZE, "receivedParts": []}), 201


@app.post("/api/uploads/<session_id>/parts")
def upload_part(session_id: str):
    try:
        meta = read_meta(session_id)
    except BadRequest as exc:
        return api_error(str(exc), 404)
    try:
        part_number = int(request.headers.get("X-Part-Number", ""))
        total_parts = int(request.headers.get("X-Total-Parts", ""))
    except ValueError:
        return api_error("缺少有效的分片编号。")
    body = request.get_data(cache=False)
    if part_number < 0 or total_parts < 1 or part_number >= total_parts or not body or len(body) > CHUNK_SIZE + 1024 * 1024:
        return api_error("上传分片无效。")
    part_path = session_dir(session_id) / "parts" / f"{part_number:06d}.part"
    part_path.write_bytes(body)
    write_meta(session_id, meta)
    return jsonify({"partNumber": part_number, "received": True})


@app.get("/api/uploads/<session_id>")
def upload_status(session_id: str):
    try:
        read_meta(session_id)
    except BadRequest as exc:
        return api_error(str(exc), 404)
    parts_dir = session_dir(session_id) / "parts"
    received = sorted(int(part.stem) for part in parts_dir.glob("*.part")) if parts_dir.exists() else []
    return jsonify({"uploadId": session_id, "receivedParts": received})


@app.post("/api/transcriptions")
def create_transcription():
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("uploadId", ""))
    try:
        meta = read_meta(session_id)
    except BadRequest as exc:
        return api_error(str(exc), 404)
    total_parts = data.get("totalParts")
    if not isinstance(total_parts, int) or total_parts < 1:
        return api_error("缺少总分片数。")
    parts = [session_dir(session_id) / "parts" / f"{n:06d}.part" for n in range(total_parts)]
    if any(not part.exists() for part in parts):
        return api_error("仍有分片未上传完成。")
    uploaded_size = sum(part.stat().st_size for part in parts)
    if uploaded_size != meta["size"]:
        return api_error("上传文件大小不一致，请重新上传。")
    original = session_dir(session_id) / "upload"
    original.mkdir(exist_ok=True)
    destination = original / meta["filename"]
    with destination.open("wb") as stream:
        for part in parts:
            with part.open("rb") as source:
                shutil.copyfileobj(source, stream)
    shutil.rmtree(session_dir(session_id) / "parts")
    try:
        duration = audio_duration_ms(destination)
    except Exception as exc:
        return api_error(f"无法读取音频：{exc}")
    if duration > MAX_DURATION_MS:
        return api_error("音频超过 30 分钟，请裁剪后再上传。")
    meta.update(status="queued", duration=duration)
    write_meta(session_id, meta)
    with _lock:
        _jobs[session_id] = {"status": "queued", "progress": 0, "message": "已上传，等待处理…"}
    threading.Thread(target=transcribe, args=(session_id,), daemon=True).start()
    return jsonify({"transcriptionId": session_id, "duration": duration}), 202


@app.get("/api/transcriptions/<session_id>")
def get_transcription(session_id: str):
    try:
        meta = read_meta(session_id)
    except BadRequest as exc:
        return api_error(str(exc), 404)
    with _lock:
        job = dict(_jobs.get(session_id, {"status": meta.get("status", "uploading"), "progress": 0, "message": ""}))
    return jsonify({"id": session_id, "duration": meta.get("duration"), "segments": meta.get("segments", []), **job})


@app.patch("/api/transcriptions/<session_id>/segments")
def update_segments(session_id: str):
    data = request.get_json(silent=True) or {}
    supplied = data.get("segments")
    if not isinstance(supplied, list) or not supplied:
        return api_error("至少保留一个句子片段。")
    try:
        meta = read_meta(session_id)
    except BadRequest as exc:
        return api_error(str(exc), 404)
    duration = int(meta.get("duration") or 0)
    cleaned: list[dict[str, Any]] = []
    previous_end = 0
    for item in supplied:
        try:
            start, end = int(item["startMs"]), int(item["endMs"])
            text = str(item["text"]).strip()
        except (KeyError, TypeError, ValueError):
            return api_error("切片字段格式无效。")
        if not text or start < 0 or end <= start or end > duration or start < previous_end:
            return api_error("切片不得重叠，且必须位于音频范围内。")
        previous_end = end
        cleaned.append({"id": str(item.get("id") or uuid.uuid4()), "startMs": start, "endMs": end, "text": text,
                        "confidence": float(item.get("confidence", 1)), "needsReview": bool(item.get("needsReview", False))})
    meta["segments"] = cleaned
    write_meta(session_id, meta)
    return jsonify({"segments": cleaned})


@app.delete("/api/transcriptions/<session_id>")
def delete_transcription(session_id: str):
    directory = session_dir(session_id)
    if directory.exists():
        shutil.rmtree(directory)
    with _lock:
        _jobs.pop(session_id, None)
    return "", 204


def cleanup_expired() -> None:
    cutoff = time.time() - SESSION_TTL_SECONDS
    for directory in WORK_DIR.iterdir():
        meta_file = directory / "meta.json"
        try:
            if meta_file.exists() and json.loads(meta_file.read_text(encoding="utf-8")).get("updatedAt", 0) < cutoff:
                shutil.rmtree(directory)
                with _lock:
                    _jobs.pop(directory.name, None)
        except (OSError, json.JSONDecodeError):
            continue


atexit.register(cleanup_expired)


def _cleanup_loop() -> None:
    while True:
        time.sleep(10 * 60)
        cleanup_expired()


cleanup_expired()
threading.Thread(target=_cleanup_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=os.environ.get("FLASK_DEBUG") == "1")
