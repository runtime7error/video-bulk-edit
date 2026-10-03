"""Logica de edicao: espelha, corta em trechos de N segundos e junta com crossfade.

Mesma logica do processar.ps1, portada para Python para rodar no servidor.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
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
    # ultimo trecho muito curto -> junta com o anterior
    if len(segs) > 1 and (segs[-1][1] - segs[-1][0]) < fade + 0.5:
        last = segs.pop()
        segs[-1][1] = last[1]
    return segs


def build_graph(info: VideoInfo, segs: list[list[float]], fade: float, transition: str) -> tuple[str, float]:
    """Retorna (filter_complex, duracao_final_esperada)."""
    n = len(segs)
    base = f"fps={info.fps},hflip,format=yuv420p,settb=AVTB"
    lines: list[str] = []
    if n == 1:
        lines.append(f"[0:v]{base}[vout]")
        if info.has_audio:
            lines.append("[0:a]anull[aout]")
        return ";\n".join(lines), info.duration

    lines.append(f"[0:v]{base},split={n}" + "".join(f"[v{k}]" for k in range(n)))
    if info.has_audio:
        lines.append(f"[0:a]aresample=async=1,asplit={n}" + "".join(f"[a{k}]" for k in range(n)))
    for k, (start, end) in enumerate(segs):
        end_opt = f":end={_num(end)}" if k < n - 1 else ""
        lines.append(f"[v{k}]trim=start={_num(start)}{end_opt},setpts=PTS-STARTPTS[s{k}]")
        if info.has_audio:
            lines.append(f"[a{k}]atrim=start={_num(start)}{end_opt},asetpts=PTS-STARTPTS[t{k}]")

    comp = segs[0][1] - segs[0][0]
    vprev, aprev = "s0", "t0"
    for k in range(1, n):
        offset = comp - fade
        vout = "vout" if k == n - 1 else f"x{k}"
        aout = "aout" if k == n - 1 else f"y{k}"
        lines.append(
            f"[{vprev}][s{k}]xfade=transition={transition}:duration={_num(fade)}:offset={_num(offset)}[{vout}]"
        )
        if info.has_audio:
            lines.append(f"[{aprev}][t{k}]acrossfade=d={_num(fade)}:c1=tri:c2=tri[{aout}]")
        comp += (segs[k][1] - segs[k][0]) - fade
        vprev, aprev = vout, aout
    return ";\n".join(lines), comp


def process_video(
    src: str,
    dst: str,
    segment: float = 5.0,
    fade: float = 0.5,
    transition: str = "fade",
    crf: int = 20,
    preset: str = "veryfast",
    threads: int = 0,
    on_progress: Optional[Callable[[float], None]] = None,
) -> dict:
    if fade >= segment:
        raise ValueError("o fade precisa ser menor que o segmento")
    info = probe(src)
    segs = make_segments(info.duration, segment, fade)
    graph, out_dur = build_graph(info, segs, fade, transition)

    fd, graph_file = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(graph)

    args = [FFMPEG, "-hide_banner", "-nostats", "-loglevel", "error", "-y",
            "-progress", "pipe:1",
            "-i", src,
            "-/filter_complex", graph_file,
            "-map", "[vout]"]
    if info.has_audio:
        args += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    args += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if threads > 0:
        args += ["-threads", str(threads), "-filter_complex_threads", str(threads)]
    args += ["-movflags", "+faststart", dst]

    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1)
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.startswith("out_time_us=") and on_progress:
                try:
                    t = int(line.split("=", 1)[1]) / 1_000_000
                    on_progress(max(0.0, min(t / out_dur, 0.999)))
                except ValueError:
                    pass
        err = proc.stderr.read() if proc.stderr else ""
        code = proc.wait()
        if code != 0:
            raise RuntimeError(f"ffmpeg falhou (codigo {code}): {err.strip()[-500:]}")
    finally:
        os.remove(graph_file)

    if on_progress:
        on_progress(1.0)
    return {"segments": len(segs), "duration_in": info.duration, "duration_out": out_dur,
            "has_audio": info.has_audio}
