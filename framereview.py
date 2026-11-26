#!/usr/bin/env python3
"""Frame Review — pause any frame of a local video, draw on it, write a note,
and get a Markdown file + annotated screenshots that an AI assistant can read.

Frame Review / 逐帧审片：本地看片，暂停在任意一帧圈画、写意见，
导出一份 Markdown 和带标注的截图，直接交给 AI 助手统一修改。

Usage / 用法:
    python3 framereview.py [VIDEO_DIR] [--port 8765] [--host 127.0.0.1] [--open]

No third-party packages. ffprobe (from FFmpeg) is optional but recommended,
it is used to read the real frame rate. 只用标准库；装了 ffprobe 能读出准确帧率。
"""
import argparse
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

__version__ = "1.0.0"

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv"}
ROOT = Path.cwd()

# Review folder sits next to the video. "_审片 / 意见.md" is the legacy (v0) naming, still read if present.
DIR_SUFFIXES = ("_review", "_审片")
MD_NAMES = ("review.md", "意见.md")

TEXT = {
    "en": {
        "title": "Review notes: {name}",
        "video": "Video",
        "shots": "Screenshots",
        "count": "{n} notes, {open} open",
        "general": "Overall notes",
        "frames": "Frame notes (in time order; #numbers match the tool)",
        "frame": "frame {f}",
        "no_text": "(no text)",
        "marks": "On-screen annotations (numbers match the screenshot):",
        "no_label": "(no label)",
        "shot": "Screenshot",
        "annotated": " (annotated)",
        "clean": "Clean frame (no annotations)",
        "rect": "box ({where}, {rng})",
        "arrow": "arrow (pointing at {where}, x {x}, y {y})",
        "text": "text ({where}, x {x}, y {y})",
        "pen": "freehand ({where}, {rng})",
        "center": "center",
        "h": ("left", "middle", "right"),
        "v": ("top", "middle", "bottom"),
        "where": "{v}-{h}",
        "sep": ": ",
    },
    "zh": {
        "title": "审片意见：{name}",
        "video": "视频",
        "shots": "截图目录",
        "count": "共 {n} 条，未处理 {open} 条",
        "general": "整体意见",
        "frames": "逐帧意见（按时间顺序，#编号和工具里一致）",
        "frame": "第 {f} 帧",
        "no_text": "（没写字）",
        "marks": "画面标注（编号和截图上的一致）：",
        "no_label": "（没配字）",
        "shot": "截图",
        "annotated": "（带标注）",
        "clean": "原画面（无标注）",
        "rect": "框（画面{where}，{rng}）",
        "arrow": "箭头（指向画面{where} x {x}，y {y}）",
        "text": "文字（画面{where} x {x}，y {y}）",
        "pen": "手画圈（画面{where}，{rng}）",
        "center": "正中",
        "h": ("左", "中", "右"),
        "v": ("上", "中", "下"),
        "where": "{h}{v}",
        "sep": "：",
    },
}
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


# ---------------------------------------------------------------- paths
def safe_path(rel: str) -> Path:
    p = (ROOT / rel).resolve()
    if p != ROOT and ROOT not in p.parents:
        raise PermissionError(rel)
    return p


def review_dir(video: Path) -> Path:
    for suf in DIR_SUFFIXES:
        d = video.parent / f"{video.stem}{suf}"
        if d.exists():
            return d
    return video.parent / f"{video.stem}{DIR_SUFFIXES[0]}"


def md_path(rd: Path) -> Path:
    for name in MD_NAMES:
        if (rd / name).exists():
            return rd / name
    return rd / MD_NAMES[0]


def is_review_dir(name: str) -> bool:
    return any(name.endswith(s) for s in DIR_SUFFIXES)


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


# ---------------------------------------------------------------- data
def probe(video: Path) -> dict:
    if not shutil.which("ffprobe"):
        return {"fps": 30.0, "fpsGuessed": True}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate,width,height:format=duration",
             "-of", "json", str(video)],
            capture_output=True, text=True, timeout=15).stdout
        j = json.loads(out)
        st = j["streams"][0]
        num, den = st["r_frame_rate"].split("/")
        return {"fps": float(num) / float(den), "width": st["width"], "height": st["height"],
                "duration": float(j.get("format", {}).get("duration", 0))}
    except Exception:
        return {"fps": 30.0, "fpsGuessed": True}


def list_videos():
    items = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".") and not is_review_dir(d)]
        for f in filenames:
            p = Path(dirpath) / f
            if p.suffix.lower() not in VIDEO_EXT or f.startswith("."):
                continue
            n = 0
            cj = review_dir(p) / "comments.json"
            if cj.exists():
                try:
                    n = len(json.loads(cj.read_text("utf-8"))["comments"])
                except Exception:
                    pass
            items.append({"path": rel(p), "mtime": p.stat().st_mtime, "comments": n})
    items.sort(key=lambda x: -x["mtime"])
    return items


def load_comments(video: Path) -> dict:
    f = review_dir(video) / "comments.json"
    if f.exists():
        return json.loads(f.read_text("utf-8"))
    return {"video": rel(video), "comments": []}


def fmt_time(t: float) -> str:
    m = int(t // 60)
    return f"{m}:{t - m * 60:06.3f}"


def circled(n: int) -> str:
    return CIRCLED[n - 1] if n <= len(CIRCLED) else f"({n})"


def where(x: float, y: float, T: dict) -> str:
    hi = 0 if x < 1 / 3 else (2 if x > 2 / 3 else 1)
    vi = 0 if y < 1 / 3 else (2 if y > 2 / 3 else 1)
    if hi == vi == 1:
        return T["center"]
    return T["where"].format(h=T["h"][hi], v=T["v"][vi])


def describe_mark(m: dict, T: dict) -> str:
    pts = m.get("pts") or [[0, 0]]
    xs = [min(max(p[0], 0), 1) for p in pts]
    ys = [min(max(p[1], 0), 1) for p in pts]
    pc = lambda a: f"{round(a * 100)}%"
    rng = f"x {pc(min(xs))}–{pc(max(xs))}" + ("，" if T is TEXT["zh"] else ", ") + f"y {pc(min(ys))}–{pc(max(ys))}"
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    t = m.get("tool")
    if t == "rect":
        return T["rect"].format(where=where(cx, cy, T), rng=rng)
    if t == "arrow":
        return T["arrow"].format(where=where(xs[-1], ys[-1], T), x=pc(xs[-1]), y=pc(ys[-1]))
    if t == "text":
        return T["text"].format(where=where(xs[0], ys[0], T), x=pc(xs[0]), y=pc(ys[0]))
    return T["pen"].format(where=where(cx, cy, T), rng=rng)


def write_md(video: Path, data: dict):
    T = TEXT.get(data.get("lang"), TEXT["en"])
    rd = review_dir(video)
    r = rel(rd)
    cs = sorted(data["comments"], key=lambda c: c["time"])
    lines = [
        "# " + T["title"].format(name=video.name), "",
        f"- {T['video']}{T['sep']}`{rel(video)}`",
        f"- {T['shots']}{T['sep']}`{r}/`",
        "- " + T["count"].format(n=len(cs), open=sum(1 for c in cs if not c.get("done"))), "",
    ]
    if data.get("general", "").strip():
        lines += ["## " + T["general"], "", data["general"].strip(), ""]
    lines += ["## " + T["frames"], ""]
    for c in cs:
        no = f"#{int(c['id'])}" if str(c.get("id", "")).isdigit() else "#?"
        mark = "[x]" if c.get("done") else "[ ]"
        fr = T["frame"].format(f=c["frame"])
        lines += [f"### {no} {mark} {fmt_time(c['time'])}" + (f"（{fr}）" if T is TEXT["zh"] else f" ({fr})"), ""]
        marks = c.get("marks") or []
        if c.get("text", "").strip():
            lines += [c["text"].strip(), ""]
        elif not marks:
            lines += [T["no_text"], ""]
        if marks:
            lines.append(T["marks"])
            for k, m in enumerate(marks, 1):
                lines.append(f"- {circled(k)} {describe_mark(m, T)}{T['sep']}{m.get('label') or T['no_label']}")
            lines.append("")
        if c.get("image"):
            lines.append(f"{T['shot']}{T['sep']}`{r}/{c['image']}`" + (T["annotated"] if marks else ""))
            if c.get("clean"):
                lines.append(f"{T['clean']}{T['sep']}`{r}/{c['clean']}`")
            lines.append("")
    md_path(rd).write_text("\n".join(lines), "utf-8")


def save_comments(video: Path, data: dict):
    rd = review_dir(video)
    rd.mkdir(exist_ok=True)
    keep = {c.get(k) for c in data["comments"] for k in ("image", "clean")}
    for png in rd.glob("*.png"):
        if png.name not in keep:
            png.unlink()
    (rd / "comments.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    write_md(video, data)


def info_payload(video: Path, data: dict) -> dict:
    rd = review_dir(video)
    return {**data, "reviewDir": rel(rd), "mdPath": rel(md_path(rd)), "mdAbs": str(md_path(rd))}


# ---------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _file(self, p: Path, ctype=None):
        size = p.stat().st_size
        ctype = ctype or mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if m:
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            elif m.group(2):
                start = max(0, size - int(m.group(2)))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        length = end - start + 1
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            with open(p, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self._file(WEB / "index.html", "text/html; charset=utf-8")
            if u.path == "/api/videos":
                return self._json({"root": ROOT.name, "videos": list_videos()})
            if u.path == "/api/info":
                v = safe_path(q["v"][0])
                return self._json({**probe(v), **info_payload(v, load_comments(v))})
            if u.path == "/video":
                return self._file(safe_path(q["v"][0]))
            if u.path == "/shot":
                v = safe_path(q["v"][0])
                return self._file(review_dir(v) / Path(q["f"][0]).name)
        except PermissionError:
            return self._json({"error": "forbidden"}, 403)
        except (KeyError, FileNotFoundError, IndexError):
            return self._json({"error": "not found"}, 404)
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        try:
            v = safe_path(body["video"])
            data = load_comments(v)
            if body.get("lang") in TEXT:
                data["lang"] = body["lang"]
            if u.path == "/api/comment":
                c = body["comment"]
                rd = review_dir(v)
                rd.mkdir(exist_ok=True)
                base = f"{c['id']}_{fmt_time(c['time']).replace(':', 'm')}s"
                for key, field, suffix in (("png", "image", ""), ("pngClean", "clean", "_clean")):
                    if key not in body:
                        continue
                    if c.get(field) and (rd / c[field]).exists():
                        (rd / c[field]).unlink()
                    c.pop(field, None)
                    if body[key]:
                        name = f"{base}{suffix}.png"
                        (rd / name).write_bytes(base64.b64decode(body[key].split(",", 1)[1]))
                        c[field] = name
                others = [x for x in data["comments"] if x["id"] != c["id"]]
                data["comments"] = sorted(others + [c], key=lambda x: x["time"])
            elif u.path == "/api/delete":
                data["comments"] = [x for x in data["comments"] if x["id"] != body["id"]]
            elif u.path == "/api/general":
                data["general"] = body.get("text", "")
            elif u.path == "/api/lang":
                pass  # only re-render the Markdown in the new language
            else:
                return self._json({"error": "not found"}, 404)
            save_comments(v, data)
            return self._json(info_payload(v, data))
        except PermissionError:
            return self._json({"error": "forbidden"}, 403)
        except KeyError:
            return self._json({"error": "bad request"}, 400)


def main():
    global ROOT
    ap = argparse.ArgumentParser(description="Frame Review — frame-accurate video review notes for AI assistants.")
    ap.add_argument("root", nargs="?", default=".", help="folder to scan for videos (default: current folder)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("FRAME_REVIEW_PORT", 8765)))
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1, local only)")
    ap.add_argument("--open", action="store_true", help="open the browser after starting")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args()
    ROOT = Path(a.root).expanduser().resolve()
    if not ROOT.is_dir():
        sys.exit(f"not a folder: {ROOT}")
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    url = f"http://{'localhost' if a.host in ('127.0.0.1', '0.0.0.0') else a.host}:{a.port}"
    print(f"Frame Review {__version__}  →  {url}\nvideos from: {ROOT}\n(Ctrl+C to stop)", flush=True)
    if not shutil.which("ffprobe"):
        print("note: ffprobe not found, assuming 30 fps (install FFmpeg for exact frame numbers)", flush=True)
    if a.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
