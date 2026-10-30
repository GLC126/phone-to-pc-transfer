#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫码传文件 · 手机 -> 电脑 局域网直传(服务端)

电脑上运行本程序,窗口里显示二维码;手机扫码打开网页,选照片/视频/文件
(或直接调摄像头拍)上传,文件落进收件箱文件夹,并写一份索引。

只用 Python 标准库 + qrcode / Pillow。界面代码在 ui_desktop.py,tkinter
起不来时自动降级成命令行模式。

用法:
    python lan_share.py                      # 打开图形窗口
    python lan_share.py --no-gui             # 命令行模式(终端里打印二维码)
    python lan_share.py --dir D:\手机传文件    # 指定收件箱
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import queue
import secrets
import shutil
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
APP_NAME = "扫码传文件"
VERSION = "1.1"
DEFAULT_PORT = 8600
HERE = Path(__file__).resolve().parent
def desktop_dir() -> Path:
    """真正的桌面路径(有些机器桌面被重定向过,所以问系统要)。"""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0 and buf.value:
            return Path(buf.value)
    except Exception:
        pass
    return Path.home() / "Desktop"
# 收到的文件就放在桌面上这个文件夹里,一眼能找到
DEFAULT_INBOX = desktop_dir() / "手机传过来的文件"
# 收件箱和 Codex 文件夹默认是同一个:所有东西都放桌面这一个文件夹,
# 不再复制两份;想额外备份到别处,再用开关换一个 Codex 目录即可。
DEFAULT_CODEX_DIR = DEFAULT_INBOX
PAGE_FILE = HERE / "page.html"
ICON_FILE = HERE / "icon.ico"
SETTINGS_FILE = HERE / "设置.json"
CHUNK_HINT = 4 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024
TOKEN_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
MAX_RECORDS = 3000
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif",
             ".tif", ".tiff", ".avif", ".svg"}
VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".3gp", ".flv", ".wmv", ".ts"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
DOC_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md",
           ".csv", ".json", ".xml", ".zip", ".rar", ".7z", ".apk", ".exe", ".epub"}
# ---------------------------------------------------------------- 小工具
def human_size(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
def kind_group(name: str, mime: str = "") -> str:
    ext = os.path.splitext(name)[1].lower()
    mime = (mime or "").lower()
    if ext in IMAGE_EXT or mime.startswith("image/"):
        return "图片"
    if ext in VIDEO_EXT or mime.startswith("video/"):
        return "视频"
    if ext in AUDIO_EXT or mime.startswith("audio/"):
        return "音频"
    if ext in DOC_EXT or mime.startswith(("text/", "application/pdf")):
        return "文档"
    return "其他"
INVALID_CHARS = set('\\/:*?"<>|\r\n\t')
def safe_name(raw: str) -> str:
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = "".join("_" if c in INVALID_CHARS else c for c in name)
    name = "".join(c for c in name if ord(c) >= 32)
    name = name.strip().rstrip(". ")
    if not name or not name.strip("."):
        return "未命名"
    stem, suffix = os.path.splitext(name)
    if len(stem) > 120:
        stem = stem[:120]
    if len(suffix) > 16:
        suffix = suffix[:16]
    return stem + suffix
def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 2
    while True:
        cand = path.with_name(f"{stem} ({i}){suffix}")
        if not cand.exists():
            return cand
        i += 1
def local_ipv4_candidates() -> list[str]:
    """本机可能被手机访问到的内网 IPv4,最可能的排前面。"""
    found: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.4)
        s.connect(("8.8.8.8", 80))      # 只是问一下默认出口网卡,不发包
        found.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except Exception:
        pass
    out: list[str] = []
    for ip in found:
        if ip in out or ip.startswith("127.") or ip.startswith("169.254."):
            continue
        out.append(ip)
    return out or ["127.0.0.1"]
def new_token(n: int = 6) -> str:
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(n))
def load_page() -> str:
    """手机端页面:优先读同目录的 page.html,读不到就用内置的精简版。"""
    html = ""
    for cand in (PAGE_FILE, Path.cwd() / "page.html"):
        try:
            if cand.exists():
                html = cand.read_text(encoding="utf-8")
                break
        except Exception:
            continue
    if not html:
        html = FALLBACK_PAGE
    return html.replace("__CHUNK__", str(CHUNK_HINT))
# ---------------------------------------------------------------- 运行状态
class State:
    def __init__(self, inbox: Path, organize: str = "date"):
        self.inbox = Path(inbox)
        self.organize = organize
        self.token = new_token()
        self.host = "127.0.0.1"
        self.port = DEFAULT_PORT
        self.started = time.time()
        self.lock = threading.Lock()
        self.records: list[dict] = []
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.parts_dir = self.inbox / ".parts"
        self.last_emit: dict[str, float] = {}
        self.codex_sync = True
        self.codex_dir = DEFAULT_CODEX_DIR
    def ensure_dirs(self) -> None:
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.parts_dir.mkdir(parents=True, exist_ok=True)
    def target_dir(self, name: str, mime: str, when: datetime) -> Path:
        if self.organize == "none":
            return self.inbox
        if self.organize == "type":
            return self.inbox / kind_group(name, mime)
        return self.inbox / when.strftime("%Y-%m-%d")
    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/s/{self.token}/"
    def add_record(self, rec: dict) -> None:
        with self.lock:
            self.records.insert(0, rec)
            del self.records[MAX_RECORDS:]
        self.events.put({"type": "received", "rec": rec})
        self.append_index(rec)
    def recent(self, n: int = 30) -> list[dict]:
        with self.lock:
            return self.records[:n]
    def totals(self) -> tuple[int, int]:
        with self.lock:
            return len(self.records), sum(r.get("size", 0) for r in self.records)
    def append_index(self, rec: dict) -> None:
        """人看收件箱.md,机器/AI 看 _index.jsonl"""
        try:
            self.ensure_dirs()
            with open(self.inbox / "_index.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({**rec, "ts": time.time()}, ensure_ascii=False) + "\n")
            md = self.inbox / "收件箱.md"
            fresh = not md.exists() or md.stat().st_size == 0
            with open(md, "a", encoding="utf-8") as fh:
                if fresh:
                    fh.write("# 收件箱\n\n手机传过来的文件,最新记录的在最下面。\n\n")
                fh.write(f"- `{rec['time']}` · {rec['rel']} · {human_size(rec['size'])}"
                         f" · 来自 {rec['client']}\n")
        except Exception:
            pass
STATE: State | None = None
def mirror_to_codex(src: Path, rec: dict) -> str | None:
    """把刚收到的文件也放进 Codex 文件夹,方便 AI 直接读。
    同一分区时用硬链接:瞬间完成、不占第二份空间。"""
    if not STATE.codex_sync:
        return None
    try:
        dest_root = Path(STATE.codex_dir)
        if dest_root.resolve() == STATE.inbox.resolve():
            return None                      # 收件箱本身就是 Codex 文件夹,不用再来一份
        when = datetime.now()
        if STATE.organize == "date":
            folder = dest_root / when.strftime("%Y-%m-%d")
        elif STATE.organize == "type":
            folder = dest_root / kind_group(src.name, "")
        else:
            folder = dest_root
        folder.mkdir(parents=True, exist_ok=True)
        dst = unique_path(folder / src.name)
        how = "复制"
        try:
            os.link(src, dst)
            how = "硬链接"
        except OSError:
            shutil.copy2(src, dst)
        with open(dest_root / "_codex_新文件.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"name": dst.name, "src": str(src), "dst": str(dst),
                                 "size": rec["size"], "time": rec["time"], "how": how},
                                ensure_ascii=False) + "\n")
        md = dest_root / "Codex 收件.md"
        fresh = not md.exists() or md.stat().st_size == 0
        with open(md, "a", encoding="utf-8") as fh:
            if fresh:
                fh.write("# Codex 收件\n\n手机传进来、可以直接让 Codex 读的文件。\n\n")
            fh.write(f"- `{rec['time']}` · {dst.name} · {human_size(rec['size'])} · {how}\n")
        return str(dst)
    except Exception as exc:
        STATE.events.put({"type": "log", "msg": f"同步到 Codex 失败:{exc}"})
        return None
def part_meta_path(uid: str) -> Path:
    return STATE.parts_dir / f"{uid}.json"
def part_data_path(uid: str) -> Path:
    return STATE.parts_dir / f"{uid}.part"
def load_part_meta(uid: str) -> dict | None:
    p = part_meta_path(uid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = f"ScanShare/{VERSION}"
    protocol_version = "HTTP/1.1"
    def log_message(self, fmt: str, *args) -> None:
        msg = fmt % args
        if "/s/" in msg:
            return                       # 页面和分片太吵,不记录
        if sys.stderr is None:           # 用 pythonw 启动时没有控制台
            return
        sys.stderr.write(f"[{datetime.now():%H:%M:%S}] {msg}\n")
    def _send(self, code: int, body: bytes = b"",
              ctype: str = "text/plain; charset=utf-8",
              extra: dict | None = None) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")
    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length <= 0 or length > 1024 * 1024:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    def _segments(self) -> list[str]:
        path = urllib.parse.urlsplit(self.path).path
        return [urllib.parse.unquote(s) for s in path.strip("/").split("/") if s]
    def _authed_rest(self) -> list[str] | None:
        segs = self._segments()
        if len(segs) < 2 or segs[0] != "s":
            return None
        if not secrets.compare_digest(segs[1], STATE.token):
            return None
        return segs[2:]
    def _query(self) -> dict:
        q = urllib.parse.urlsplit(self.path).query
        return {k: v[0] for k, v in urllib.parse.parse_qs(q).items()}
    # -------------------------------------------------- GET
    def do_GET(self) -> None:            # noqa: N802
        segs = self._segments()
        raw_path = urllib.parse.urlsplit(self.path).path
        # 少了结尾的斜杠就补上,否则页面里的相对路径会算错
        if (len(segs) == 2 and segs[0] == "s"
                and secrets.compare_digest(segs[1], STATE.token)
                and not raw_path.endswith("/")):
            self._send(301, b"", extra={"Location": f"/s/{STATE.token}/"})
            return
        rest = self._authed_rest()
        if rest is None:
            self._send(403, WELCOME, "text/html; charset=utf-8")
            return
        if rest == []:
            self._send(200, load_page().encode("utf-8"), "text/html; charset=utf-8")
            return
        if rest == ["favicon.ico"]:
            self._send(200, FAVICON, "image/svg+xml")
            return
        if rest == ["icon.png"]:
            try:
                data = (HERE / "icon.png").read_bytes()
                self._send(200, data, "image/png")
            except Exception:
                self._send(404, b"404")
            return
        if rest == ["api", "state"]:
            count, total = STATE.totals()
            try:
                same = Path(STATE.codex_dir).resolve() == Path(STATE.inbox).resolve()
            except Exception:
                same = True
            self._json(200, {"ok": True, "count": count, "total": total,
                             "sync": bool(STATE.codex_sync),
                             "codex_same": same,
                             "codex": str(STATE.codex_dir),
                             "received": STATE.recent(30)})
            return
        self._send(404, b"404")
    # -------------------------------------------------- POST
    def do_POST(self) -> None:           # noqa: N802
        rest = self._authed_rest()
        if rest is None:
            self._send(403, b"403")
            return
        if rest == ["api", "init"]:
            self.api_init()
        elif rest == ["api", "chunk"]:
            self.api_chunk()
        elif rest == ["api", "finish"]:
            self.api_finish()
        else:
            self._send(404, b"404")
    # -------------------------------------------------- 三个接口
    def api_init(self) -> None:
        """握手:给这个文件分配一个上传会话。同一个文件(同名+同大小+同修改时间)
        再问一次会拿到上次传到的位置,所以断了能接着传。"""
        data = self._read_json()
        name = safe_name(data.get("name") or "未命名")
        try:
            size = int(data.get("size") or 0)
            mtime = int(data.get("mtime") or 0)
        except Exception:
            self._json(400, {"ok": False, "error": "参数不对"})
            return
        if size <= 0:
            self._json(400, {"ok": False, "error": "空文件"})
            return
        mime = str(data.get("kind") or "")
        uid = hashlib.sha1(f"{name}|{size}|{mtime}".encode("utf-8")).hexdigest()[:16]
        STATE.ensure_dirs()
        meta = load_part_meta(uid)
        data_path = part_data_path(uid)
        offset = data_path.stat().st_size if data_path.exists() else 0
        if not meta:
            meta = {"id": uid, "name": name, "size": size, "mtime": mtime,
                    "mime": mime, "created": time.time()}
            part_meta_path(uid).write_text(json.dumps(meta, ensure_ascii=False),
                                           encoding="utf-8")
        if 0 < offset < size:
            STATE.events.put({"type": "log",
                              "msg": f"接着上次继续传:{name}(已有 {human_size(offset)})"})
        elif offset >= size:
            offset = 0
            try:
                data_path.unlink()
            except Exception:
                pass
        STATE.events.put({"type": "send_start", "id": uid, "name": name,
                          "size": size, "done": offset, "kind": kind_group(name, mime)})
        self._json(200, {"ok": True, "id": uid, "offset": offset, "chunk": CHUNK_HINT})
    def api_chunk(self) -> None:
        q = self._query()
        uid = q.get("id") or ""
        try:
            offset = int(q.get("offset") or 0)
        except Exception:
            self._json(400, {"ok": False, "error": "offset 无效"})
            return
        meta = load_part_meta(uid) if uid else None
        if not meta:
            self._json(409, {"ok": False, "error": "上传会话不存在"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length <= 0 or length > MAX_CHUNK:
            self._json(400, {"ok": False, "error": "分片大小不合法"})
            return
        data_path = part_data_path(uid)
        try:
            have = data_path.stat().st_size if data_path.exists() else 0
            if offset > have:
                self._json(409, {"ok": False, "error": "offset 超出已收内容", "have": have})
                return
            with open(data_path, "r+b" if data_path.exists() else "wb") as fh:
                if offset < have:
                    fh.truncate(offset)
                fh.seek(offset)
                remaining = length
                while remaining > 0:
                    block = self.rfile.read(min(256 * 1024, remaining))
                    if not block:
                        break
                    fh.write(block)
                    remaining -= len(block)
                fh.flush()
                os.fsync(fh.fileno())
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            self._json(500, {"ok": False, "error": f"写入失败: {exc}"})
            return
        got = data_path.stat().st_size
        total = int(meta.get("size") or 0)
        now = time.time()
        if got >= total or now - STATE.last_emit.get(uid, 0.0) > 0.3:
            STATE.last_emit[uid] = now
            STATE.events.put({"type": "send_progress", "id": uid, "name": meta.get("name", ""),
                              "size": total, "done": got})
        self._json(200, {"ok": True, "id": uid, "received": got})
    def api_finish(self) -> None:
        data = self._read_json()
        uid = str(data.get("id") or "")
        meta = load_part_meta(uid) if uid else None
        if not meta:
            self._json(409, {"ok": False, "error": "上传会话不存在"})
            return
        data_path = part_data_path(uid)
        have = data_path.stat().st_size if data_path.exists() else 0
        want = int(meta.get("size") or 0)
        if have != want:
            self._json(409, {"ok": False, "error": "文件不完整,请重试",
                             "have": have, "want": want})
            return
        now = datetime.now()
        name = safe_name(meta.get("name") or "未命名")
        folder = STATE.target_dir(name, meta.get("mime") or "", now)
        folder.mkdir(parents=True, exist_ok=True)
        final = unique_path(folder / name)
        try:
            os.replace(data_path, final)
        except OSError:
            try:
                final.write_bytes(data_path.read_bytes())
                data_path.unlink()
            except Exception as exc:
                self._json(500, {"ok": False, "error": f"保存失败: {exc}"})
                return
        try:
            part_meta_path(uid).unlink()
        except Exception:
            pass
        STATE.last_emit.pop(uid, None)
        if meta.get("mtime"):
            try:
                os.utime(final, (time.time(), float(meta["mtime"]) / 1000.0))
            except Exception:
                pass
        rec = {
            "id": uid,
            "name": final.name,
            "path": str(final),
            "rel": str(final.relative_to(STATE.inbox)),
            "size": want,
            "kind": kind_group(final.name, meta.get("mime") or ""),
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "client": self.client_address[0],
        }
        STATE.events.put({"type": "send_done", "id": uid})
        synced = mirror_to_codex(final, rec)
        STATE.add_record(rec)
        if synced:
            STATE.events.put({"type": "synced", "name": final.name, "dst": synced})
        self._json(200, {"ok": True, "name": final.name, "rel": rec["rel"]})
FAVICON = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    b'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    b'<stop offset="0" stop-color="#2563eb"/><stop offset="1" stop-color="#4f46e5"/>'
    b'</linearGradient></defs>'
    b'<rect width="64" height="64" rx="15" fill="url(#g)"/>'
    b'<g fill="#fff"><rect x="10" y="10" width="15" height="15" rx="5"/>'
    b'<rect x="39" y="10" width="15" height="15" rx="5"/>'
    b'<rect x="10" y="39" width="15" height="15" rx="5"/>'
    b'<rect x="15" y="15" width="5" height="5" rx="2" fill="#2f5de0"/>'
    b'<rect x="44" y="15" width="5" height="5" rx="2" fill="#2f5de0"/>'
    b'<rect x="15" y="44" width="5" height="5" rx="2" fill="#2f5de0"/>'
    b'<rect x="39" y="39" width="6" height="6" rx="2"/>'
    b'<rect x="48" y="39" width="6" height="6" rx="2"/>'
    b'<rect x="39" y="48" width="6" height="6" rx="2"/>'
    b'<rect x="48" y="48" width="6" height="6" rx="2"/></g></svg>'
)
WELCOME = (
    "<!DOCTYPE html><html lang='zh-CN'><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>扫码传文件</title><body style='margin:0;background:#0a0f1e;color:#eef2fb;"
    "font-family:-apple-system,\"PingFang SC\",\"Microsoft YaHei\",sans-serif;"
    "padding:48px 24px;line-height:1.8'>"
    "<h2 style='font-size:20px;margin:0 0 12px'>这个地址不对</h2>"
    "<p style='color:#8e9cbd;font-size:14px'>请用电脑窗口里那个二维码重新扫一次。"
    "链接是带口令的,少一个字符都进不来。</p></body></html>"
).encode("utf-8")
FALLBACK_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>传到电脑</title></head><body style="background:#0a0f1e;color:#eef2fb;
font-family:sans-serif;padding:24px">
<h2>选文件上传</h2>
<input type="file" id="f" multiple>
<p id="s" style="color:#8e9cbd"></p>
<script>
document.getElementById('f').addEventListener('change', function(e){
  var files = e.target.files, i = 0;
  function next(){
    if (i >= files.length) { document.getElementById('s').textContent = '全部完成'; return; }
    var f = files[i++], off = 0, id = null, CH = __CHUNK__;
    fetch('api/init', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name:f.name, size:f.size, mtime:f.lastModified||0})})
    .then(function(r){ return r.json(); }).then(function(j){
      id = j.id; off = j.offset || 0;
      function step(){
        if (off >= f.size) {
          fetch('api/finish', {method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({id:id})}).then(next);
          return;
        }
        var end = Math.min(off + CH, f.size);
        fetch('api/chunk?id=' + id + '&offset=' + off, {method:'POST', body: f.slice(off, end)})
          .then(function(){ off = end;
            document.getElementById('s').textContent = f.name + ' ' + Math.floor(off/f.size*100) + '%';
            step(); });
      }
      step();
    });
  }
  next();
});
</script></body></html>"""
# ---------------------------------------------------------------- 服务器
class LocalServer(ThreadingHTTPServer):
    """Windows 上 SO_REUSEADDR 会让两个实例绑同一个端口(看起来像"程序没更新"),
    所以这里改成独占:端口被占就自动往后找。"""
    allow_reuse_address = os.name != "nt"
    daemon_threads = True
def make_server(state: State, port: int, tries: int = 20) -> ThreadingHTTPServer:
    last_exc = None
    for i in range(tries):
        p = port + i
        try:
            srv = LocalServer(("0.0.0.0", p), Handler)
            srv.daemon_threads = True
            state.port = p
            return srv
        except OSError as exc:
            last_exc = exc
            continue
    raise RuntimeError(f"端口被占用,{port} ~ {port + tries - 1} 都试过:{last_exc}")
# ---------------------------------------------------------------- 命令行模式
def run_console(state: State) -> None:
    try:
        import qrcode
        qr = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(state.url)
        qr.make()
        qr.print_ascii(invert=True)
    except Exception:
        print("(没装 qrcode,二维码打不出来,手动输入下面的网址吧)")
    print(f"\n  {APP_NAME} v{VERSION}")
    print(f"  手机浏览器打开:{state.url}")
    print(f"  收件箱:{state.inbox}")
    print("  按 Ctrl+C 退出\n")
    marks: dict[str, int] = {}
    try:
        while True:
            try:
                ev = state.events.get(timeout=0.5)
            except queue.Empty:
                continue
            kind = ev.get("type")
            if kind == "received":
                rec = ev["rec"]
                print(f"  [收到] {rec['time']}  {rec['rel']}  ({human_size(rec['size'])})")
            elif kind == "send_start":
                print(f"  [开始] {ev['name']}  ({human_size(ev['size'])})"
                      + (f"  从 {human_size(ev['done'])} 处续传" if ev.get("done") else ""))
                marks[ev["id"]] = 0
            elif kind == "send_progress":
                total = ev["size"] or 1
                step = int(ev["done"] * 100 / total) // 25
                if step > marks.get(ev["id"], 0):
                    marks[ev["id"]] = step
                    print(f"  [传送] {ev['name']}  {step * 25}%")
            elif kind == "log":
                print(f"  [提示] {ev['msg']}")
    except KeyboardInterrupt:
        print("\n  已退出。")
# ---------------------------------------------------------------- 配置与入口
AGENTS_MARK = "<!-- 由「扫码传文件」自动生成;删掉或改写都不会影响程序 -->"
AGENTS_BEGIN = "<!-- BEGIN 扫码传文件(自动生成,可删) -->"
AGENTS_END = "<!-- END 扫码传文件 -->"
def agents_targets() -> list[Path]:
    """新开的对话会自动读 AGENTS.md,所以把"文件在哪"写在这里。"""
    out = [HERE.parent / "AGENTS.md"]
    for parent in HERE.parents:
        if parent.name == "Codex":
            out.append(parent / "AGENTS.md")
            break
    out.append(Path.home() / ".codex" / "AGENTS.md")     # 全局,所有对话都会看
    return out
def write_pointer_docs() -> None:
    """更新给 AI 看的说明:文件收在哪、索引长什么样、怎么读。"""
    if STATE is None:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    block = (
        f"{AGENTS_BEGIN}\n"
        "# 这台电脑上的「扫码传文件」\n\n"
        "用户会用手机拍照/选文件,扫码直接传到这台电脑。**收到的文件都在:**\n\n"
        f"    {STATE.inbox}\n\n"
        f"- 按日期分子文件夹(例如 `{today}\\`),文件名保留手机上的原名\n"
        "- `_index.jsonl`:每个文件一条 JSON(name / rel / path / size / kind / time / client)\n"
        "- `收件箱.md`:给人看的清单\n"
        "- `.parts\\`:上传中的临时分片,不要动\n\n"
        "用户说「看看刚传的」「处理下传过来的照片」这类话时,**直接去上面那个文件夹读**,\n"
        "不要问用户要文件;不确定收了什么就先看 `_index.jsonl` 的最后几行。\n"
        "如果当前会话读不了桌面,用 request_permissions 申请读权限,"
        "或者让用户把文件夹路径贴进来。\n\n"
        f"工具程序在:`{HERE}`(用法见同目录 `使用说明.md`)\n"
        f"{AGENTS_END}"
    )
    for target in agents_targets():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            old = target.read_text(encoding="utf-8", errors="ignore") if target.exists() else ""
            if AGENTS_BEGIN in old and AGENTS_END in old:
                start = old.index(AGENTS_BEGIN)
                end = old.index(AGENTS_END) + len(AGENTS_END)
                new = old[:start] + block + old[end:]
            elif old.strip():
                new = old.rstrip() + "\n\n" + block + "\n"   # 用户自己写的,只追加不覆盖
            else:
                new = block + "\n"
            target.write_text(new, encoding="utf-8")
        except Exception:
            pass
def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
def save_settings() -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(
            {"inbox": str(STATE.inbox), "organize": STATE.organize,
             "codex_sync": bool(STATE.codex_sync), "codex_dir": str(STATE.codex_dir)},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    write_pointer_docs()
def fatal_box(msg: str) -> None:
    """用 pythonw 启动时没有控制台,出错就弹个框,不然像是什么都没发生。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, f"{APP_NAME} 启动失败", 0x10)
    except Exception:
        print(msg)
def main() -> int:
    global STATE
    ap = argparse.ArgumentParser(description=f"{APP_NAME} · 手机扫码传文件到电脑")
    ap.add_argument("--dir", help="收件箱目录")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"端口,默认 {DEFAULT_PORT}")
    ap.add_argument("--organize", choices=["date", "type", "none"], default=None,
                    help="收件箱内怎么整理:date 按日期(默认)/ type 按类型 / none 平铺")
    ap.add_argument("--no-gui", action="store_true", help="不开窗口,用命令行模式")
    ap.add_argument("--open-browser", action="store_true", help="启动时在电脑上也打开一次页面")
    args = ap.parse_args()
    saved = load_settings()
    inbox = Path(args.dir) if args.dir else Path(saved.get("inbox") or DEFAULT_INBOX)
    organize = args.organize or saved.get("organize") or "date"
    STATE = State(inbox, organize)
    STATE.codex_sync = bool(saved.get("codex_sync", True))
    STATE.codex_dir = Path(saved.get("codex_dir") or DEFAULT_CODEX_DIR)
    write_pointer_docs()
    try:
        STATE.ensure_dirs()
    except Exception as exc:
        fatal_box(f"收件箱目录建不出来:\n{STATE.inbox}\n\n{exc}")
        return 1
    STATE.host = local_ipv4_candidates()[0]
    try:
        srv = make_server(STATE, args.port)
    except Exception as exc:
        fatal_box(str(exc))
        return 1
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"{APP_NAME} v{VERSION}")
    print(f"收件箱:{STATE.inbox}")
    if STATE.codex_sync:
        same = Path(STATE.codex_dir).resolve() == STATE.inbox.resolve()
        print(f"Codex :{'就是收件箱' if same else STATE.codex_dir}")
    print(f"地址  :{STATE.url}")
    if args.open_browser:
        webbrowser.open(STATE.url)
    if args.no_gui:
        run_console(STATE)
    else:
        try:
            import ui_desktop
            ui_desktop.run(STATE)
        except Exception as exc:
            if sys.executable.lower().endswith("pythonw.exe"):
                fatal_box("窗口没起来,没有控制台看不到日志。\n"
                          "请改用「启动扫码传文件.bat」启动一次,就能看到详细报错。\n\n"
                          f"{exc}")
                return 1
            else:
                print(f"\n窗口起不来({exc}),改用命令行模式。")
                run_console(STATE)
    save_settings()
    srv.shutdown()
    return 0
if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
