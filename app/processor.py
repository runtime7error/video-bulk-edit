"""Logica de edicao: espelha, corta em trechos de N segundos e junta com crossfade.

Pipeline de ultra-baixo consumo de memoria RAM (< 80 MB):
Em vez de criar um grafo unico com split=50 (que estoura os 512 MB do Render),
o processador divide o fluxo em fragmentos e transicoes independentes,
concatenando tudo ao final.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


def _bin(name: str) -> str:
    env = os.environ.get(f"{name.upper()}_PATH")
    if env:
        return env
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"{name} nao encontrado no PATH")
    return found


FFMPEG = _bin("ffmpeg")
FFPROBE = _bin("ffprobe")


def _num(x: float) -> str:
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return s or "0"


def _probe(path: str, *args: str) -> str:
    r = subprocess.run(
        [FFPROBE, "-v", "error", *args, "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True,
    )
    out = r.stdout.strip().splitlines()
    return out[0].strip() if out else ""


@dataclass
class VideoInfo:
    duration: float
    fps: str
    has_audio: bool


def probe(path: str) -> VideoInfo:
    try:
        dur = float(_probe(path, "-show_entries", "format=duration"))
    except ValueError:
        raise RuntimeError("nao foi possivel ler a duracao do video (arquivo invalido?)")
    if dur <= 0:
        raise RuntimeError("duracao invalida")
    fps = _probe(path, "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate")
    if not fps or fps.startswith("0"):
        fps = "30"
    has_audio = bool(_probe(path, "-select_streams", "a:0", "-show_entries", "stream=index"))
    return VideoInfo(dur, fps, has_audio)


def make_segments(duration: float, segment: float, fade: float) -> list[list[float]]:
    segs: list[list[float]] = []
    t = 0.0
    while t < duration - 0.001:
        end = min(t + segment, duration)
        segs.append([t, end])
        t = end
    # Se o ultimo trecho for muito curto, junta com o anterior
    if len(segs) > 1 and (segs[-1][1] - segs[-1][0]) < fade + 0.5:
        last = segs.pop()
        segs[-1][1] = last[1]
    return segs


def process_video(
    src: str,
    dst: str,
    segment: float = 5.0,
    fade: float = 0.5,
    transition: str = "fade",
    crf: int = 20,
    preset: str = "ultrafast",
    threads: int = 1,
    on_progress: Optional[Callable[[float], None]] = None,
) -> dict:
    if fade >= segment:
        raise ValueError("o fade precisa ser menor que o segmento")

    info = probe(src)
    segs = make_segments(info.duration, segment, fade)
    n = len(segs)
    expected_out_dur = info.duration - (max(0, n - 1) * fade)

    # Pasta temporaria para os fragmentos
    temp_dir = tempfile.mkdtemp(prefix="autocutter_chunks_")
    th = str(threads if threads > 0 else 1)

    v_enc = [
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-threads", th, "-pix_fmt", "yuv420p", "-r", info.fps
    ]
    a_enc = ["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"] if info.has_audio else []

    parts: list[str] = []
    list_file = os.path.join(temp_dir, "concat_list.txt")

    total_steps = (2 * n - 1) if n > 1 else 1
    current_step = 0

    def step_done():
        nonlocal current_step
        current_step += 1
        if on_progress:
            on_progress(min(0.98, current_step / (total_steps + 1)))

    try:
        if n == 1:
            # Apenas 1 trecho: espelha direto
            cmd = [
                FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-threads", th,
                "-i", src, "-vf", "hflip,format=yuv420p",
                *v_enc, *a_enc, "-movflags", "+faststart", dst
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg erro: {r.stderr[:400]}")
            if on_progress:
                on_progress(1.0)
            return {
                "segments": 1,
                "duration_in": info.duration,
                "duration_out": info.duration,
                "has_audio": info.has_audio,
            }

        # 1. Primeiro corpo: [0, fim_0 - fade]
        dur0 = (segs[0][1] - segs[0][0]) - fade
        p0 = os.path.join(temp_dir, "chunk_body_0.mp4")
        cmd = [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-threads", th,
            "-ss", _num(segs[0][0]), "-i", src, "-t", _num(dur0),
            "-vf", "hflip,format=yuv420p", *v_enc, *a_enc, p0
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg erro chunk 0: {r.stderr[:400]}")
        parts.append(p0)
        step_done()

        # 2. Transicoes e Corpos intermediarios
        for k in range(n - 1):
            tail_start = segs[k][1] - fade
            head_start = segs[k + 1][0]

            # Transicao de k -> k+1 (apenas fade segundos)
            t_file = os.path.join(temp_dir, f"chunk_trans_{k}.mp4")
            fc_lines = [
                f"[0:v]hflip,format=yuv420p,setpts=PTS-STARTPTS[v0]",
                f"[1:v]hflip,format=yuv420p,setpts=PTS-STARTPTS[v1]",
                f"[v0][v1]xfade=transition={transition}:duration={_num(fade)}:offset=0[vout]"
            ]
            maps = ["-map", "[vout]"]
            if info.has_audio:
                fc_lines.extend([
                    "[0:a]asetpts=PTS-STARTPTS[a0]",
                    "[1:a]asetpts=PTS-STARTPTS[a1]",
                    f"[a0][a1]acrossfade=d={_num(fade)}:c1=tri:c2=tri[aout]"
                ])
                maps.extend(["-map", "[aout]"])

            cmd = [
                FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-threads", th,
                "-ss", _num(tail_start), "-i", src, "-t", _num(fade),
                "-ss", _num(head_start), "-i", src, "-t", _num(fade),
                "-filter_complex", ";".join(fc_lines), *maps,
                *v_enc, *a_enc, t_file
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg erro transicao {k}: {r.stderr[:400]}")
            parts.append(t_file)
            step_done()

            # Corpo de k+1
            b_file = os.path.join(temp_dir, f"chunk_body_{k + 1}.mp4")
            b_start = segs[k + 1][0] + fade
            b_end = segs[k + 1][1] - (fade if k + 1 < n - 1 else 0)
            b_dur = b_end - b_start

            if b_dur > 0.04:
                cmd = [
                    FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-threads", th,
                    "-ss", _num(b_start), "-i", src, "-t", _num(b_dur),
                    "-vf", "hflip,format=yuv420p", *v_enc, *a_enc, b_file
                ]
                r = subprocess.run(cmd, capture_output=True, text=True)
                if r.returncode != 0:
                    raise RuntimeError(f"ffmpeg erro corpo {k+1}: {r.stderr[:400]}")
                parts.append(b_file)
            step_done()

        # 3. Concatenacao final de todas as partes (stream copy, instantaneo!)
        with open(list_file, "w", encoding="utf-8") as f:
            for p in parts:
                p_escaped = p.replace("\\", "/").replace("'", "'\\''")
                f.write(f"file '{p_escaped}'\n")

        cmd = [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", list_file,
            "-c", "copy", "-movflags", "+faststart", dst
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg erro concat final: {r.stderr[:400]}")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    if on_progress:
        on_progress(1.0)

    return {
        "segments": n,
        "duration_in": info.duration,
        "duration_out": expected_out_dur,
        "has_audio": info.has_audio,
    }
