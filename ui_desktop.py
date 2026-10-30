# -*- coding: utf-8 -*-
"""扫码传文件 · 电脑端窗口(画布自绘:圆角卡片 / 矢量图标 / 按 DPI 缩放)"""
from __future__ import annotations
import os
import queue
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
core = sys.modules.get("lan_share") or sys.modules["__main__"]
try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageTk
    PIL_OK = True
except Exception:
    PIL_OK = False
# ---------------------------------------------------------------- 主题
BG_TOP = (11, 15, 28)
BG_BOT = (7, 10, 19)
CARD_TOP = (20, 27, 44)
CARD_BOT = (15, 21, 34)
CARD2_TOP = (26, 34, 53)
CARD2_BOT = (20, 27, 43)
LINE = (39, 51, 76)
LINE_SOFT = (30, 40, 60)
A1 = (79, 124, 255)
A2 = (139, 108, 255)
OK_C = (52, 211, 153)
ERR_C = (248, 113, 113)
FG = "#e9eefb"
FG2 = "#c3cbe4"
DIM = "#8b96b5"
FAINT = "#5b6684"
UI_FONT = "Microsoft YaHei UI"
KIND_INFO = {
    "图片": ("image", (59, 130, 246)),
    "视频": ("video", (139, 92, 246)),
    "音频": ("audio", (34, 211, 238)),
    "文档": ("doc", (245, 158, 11)),
    "其他": ("other", (148, 163, 184)),
}
FONT_REG = r"C:\Windows\Fonts\msyh.ttc"
FONT_BLD = r"C:\Windows\Fonts\msyhbd.ttc"
# ---------------------------------------------------------------- 图像工具
_cache: dict = {}
_fonts: dict = {}
def mix(c1, c2, t):
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1, c2))
def pil_font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        path = FONT_BLD if bold else FONT_REG
        try:
            _fonts[key] = ImageFont.truetype(path, size)
        except Exception:
            _fonts[key] = ImageFont.load_default()
    return _fonts[key]
def grad_round(w, h, radius, top, bot, outline=None, border=1):
    """竖向渐变 + 圆角,返回 RGBA"""
    w, h = max(1, int(w)), max(1, int(h))
    key = ("g", w, h, radius, top, bot, outline, border)
    if key in _cache:
        return _cache[key]
    grad = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(grad)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=mix(top, bot, y / max(1, h - 1)))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=int(radius), fill=255)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    if outline:
        ImageDraw.Draw(img).rounded_rectangle([0, 0, w - 1, h - 1], radius=int(radius),
                                              outline=outline, width=border)
    _cache[key] = img
    return img
def flat_round(w, h, radius, fill, outline=None, border=1):
    w, h = max(1, int(w)), max(1, int(h))
    key = ("f", w, h, radius, fill, outline, border)
    if key in _cache:
        return _cache[key]
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, w - 1, h - 1], radius=int(radius),
                                          fill=fill, outline=outline, width=border)
    _cache[key] = img
    return img
def shadow_box(w, h, radius, blur, alpha, dy):
    key = ("s", w, h, radius, blur, alpha, dy)
    if key in _cache:
        return _cache[key]
    pad = blur * 2
    img = Image.new("RGBA", (w + pad * 2, h + pad * 2 + dy), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle(
        [pad, pad + dy, pad + w - 1, pad + h - 1 + dy], radius=int(radius), fill=(0, 0, 0, alpha))
    img = img.filter(ImageFilter.GaussianBlur(blur))
    _cache[key] = img
    return img
def _stroke(d, box, radius, color, lw):
    d.rounded_rectangle(box, radius=radius, outline=color, width=lw)
def glyph(d, kind, px, color):
    """在 px 见方的区域里画一个矢量图标"""
    lw = max(2, int(px * 0.072))
    c = (*color, 240)
    a = px * 0.27
    b = px - a
    w = b - a
    if kind == "image":
        _stroke(d, [a, a + w * 0.05, b, b - w * 0.05], int(px * 0.08), c, lw)
        r = w * 0.115
        cx, cy = a + w * 0.30, a + w * 0.32
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
        d.line([(a + w * 0.10, b - w * 0.20), (a + w * 0.44, a + w * 0.50),
                (a + w * 0.68, b - w * 0.20)], fill=c, width=lw, joint="curve")
    elif kind == "video":
        d.polygon([(a + w * 0.10, a + w * 0.05), (b, px / 2), (a + w * 0.10, b - w * 0.05)], fill=c)
    elif kind == "audio":
        r = w * 0.155
        c1x, c2x = a + w * 0.22, a + w * 0.68
        cy = b - w * 0.18
        d.ellipse([c1x - r, cy - r, c1x + r, cy + r], fill=c)
        d.ellipse([c2x - r, cy - r, c2x + r, cy + r], fill=c)
        d.line([(c1x + r * 0.85, cy), (c1x + r * 0.85, a + w * 0.04)], fill=c, width=lw)
        d.line([(c2x + r * 0.85, cy), (c2x + r * 0.85, a + w * 0.24)], fill=c, width=lw)
        d.line([(c1x + r * 0.85, a + w * 0.04), (c2x + r * 0.85, a + w * 0.24)], fill=c, width=lw)
    elif kind == "doc":
        _stroke(d, [a + w * 0.06, a, b - w * 0.06, b], int(px * 0.08), c, lw)
        for i, frac in enumerate((0.36, 0.55, 0.74)):
            x1 = a + w * 0.24
            x2 = b - w * (0.30 if i == 2 else 0.22)
            y = a + w * frac
            d.line([(x1, y), (x2, y)], fill=c, width=max(1, lw - 1))
    else:
        r = w * 0.10
        cy = px / 2
        for i in range(3):
            cx = a + w * (0.18 + i * 0.32)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
    return d
def badge_image(kind, px):
    key = ("b", kind, px)
    if key in _cache:
        return _cache[key]
    color = KIND_INFO.get(kind, KIND_INFO["其他"])[1]
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, px - 1, px - 1], radius=int(px * 0.30),
                        fill=(*color, 38), outline=(*color, 78), width=max(1, px // 26))
    glyph(d, KIND_INFO.get(kind, KIND_INFO["其他"])[0], px, color)
    _cache[key] = img
    return img
def button_image(text, w, h, style, state, fpx):
    key = ("btn", text, w, h, style, state, fpx)
    if key in _cache:
        return _cache[key]
    radius = int(h * 0.34)
    if style == "primary":
        top, bot = A1, A2
        if state == "hover":
            top, bot = mix(A1, (255, 255, 255), .12), mix(A2, (255, 255, 255), .12)
        elif state == "press":
            top, bot = mix(A1, (0, 0, 0), .12), mix(A2, (0, 0, 0), .12)
        img = grad_round(w, h, radius, top, bot, outline=(255, 255, 255, 46), border=1).copy()
        txt, tcol = text, (255, 255, 255, 255)
        hi = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(hi).rounded_rectangle([1, 1, w - 2, max(2, int(h * 0.45))],
                                             radius=radius, fill=(255, 255, 255, 26))
        img.alpha_composite(hi)
    elif style == "ghost":
        fill = (255, 255, 255, 20) if state != "hover" else (255, 255, 255, 34)
        img = flat_round(w, h, radius, fill, outline=(*LINE, 255), border=1).copy()
        txt, tcol = text, (233, 238, 251, 255)
    else:
        img = flat_round(w, h, radius, (255, 255, 255, 0)).copy()
        txt, tcol = text, (139, 150, 181, 255) if state != "hover" else (233, 238, 251, 255)
    d = ImageDraw.Draw(img)
    f = pil_font(fpx)
    bb = d.textbbox((0, 0), txt, font=f)
    d.text(((w - (bb[2] - bb[0])) / 2 - bb[0], (h - (bb[3] - bb[1])) / 2 - bb[1]),
           txt, font=f, fill=tcol)
    _cache[key] = img
    return img
def bar_track(w, h):
    return flat_round(w, h, h // 2, (255, 255, 255, 18))
def bar_fill(w, h, frac, c1, c2):
    """按 1/64 量化,避免每次都新建图片"""
    steps = max(1, int(round(max(0.0, min(1.0, frac)) * 64)))
    frac = steps / 64
    full = int(w * frac)
    width = max(h, full) if frac > 0 else 0
    if width <= 0:
        return None
    key = ("fill", w, h, steps, c1, c2)
    if key in _cache:
        return _cache[key]
    img = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    grad = Image.new("RGB", (width, h))
    d = ImageDraw.Draw(grad)
    for x in range(width):
        d.line([(x, 0), (x, h)], fill=mix(c1, c2, x / max(1, width - 1)))
    mask = Image.new("L", (width, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, width - 1, h - 1], radius=h // 2, fill=255)
    img.paste(grad, (0, 0), mask)
    _cache[key] = img
    return img
def ellipsis(text, limit):
    return text if len(text) <= limit else text[:limit - 1] + "…"
def work_area() -> tuple[int, int, int, int] | None:
    """屏幕可用区域(不含任务栏),物理像素。"""
    try:
        import ctypes
        from ctypes import wintypes
        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]
        r = RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
            if r.right - r.left > 200 and r.bottom - r.top > 200:
                return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        pass
    return None
def copy_to_clipboard(path: Path) -> tuple[bool, str]:
    """把文件放进剪贴板:
    - CF_HDROP:资源管理器 / 微信 / QQ 里可以直接粘贴这个文件
    - CF_DIB:图片再额外放一份位图,Word、聊天窗口里能直接粘出图来
    """
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p
    GMEM_MOVEABLE = 0x0002
    try:
        CF_HDROP = u32.RegisterClipboardFormatW("CF_HDROP")
        files = ctypes.create_unicode_buffer(str(path) + "\0")
        text_bytes = len(files) * ctypes.sizeof(ctypes.c_wchar)
        head = 20
        size = head + text_bytes + ctypes.sizeof(ctypes.c_wchar)
        hdrop = k32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not hdrop:
            return False, "内存分配失败"
        ptr = k32.GlobalLock(hdrop)
        ctypes.memset(ptr, 0, size)
        p = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32))
        p[0] = head          # pFiles:文件列表相对这个结构的偏移
        ctypes.cast(ctypes.c_void_p(ptr + 16), ctypes.POINTER(ctypes.c_int))[0] = 1  # fWide
        ctypes.memmove(ctypes.c_void_p(ptr + head), files, text_bytes + ctypes.sizeof(ctypes.c_wchar))
        k32.GlobalUnlock(hdrop)
        dib_mem = None
        if PIL_OK and path.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"):
            try:
                im = Image.open(path)
                im = ImageOps.exif_transpose(im)
                if max(im.size) > 4096:
                    im.thumbnail((4096, 4096), Image.LANCZOS)
                if im.mode in ("RGBA", "LA", "P"):
                    im = im.convert("RGBA")
                    bg = Image.new("RGB", im.size, (255, 255, 255))
                    bg.paste(im, mask=im.split()[-1])
                    im = bg
                else:
                    im = im.convert("RGB")
                import io
                buf = io.BytesIO()
                im.save(buf, format="BMP")
                raw = buf.getvalue()[14:]        # 去掉 BMP 文件头,剩下就是 DIB
                dib_mem = k32.GlobalAlloc(GMEM_MOVEABLE, len(raw))
                if dib_mem:
                    dptr = k32.GlobalLock(dib_mem)
                    ctypes.memmove(ctypes.c_void_p(dptr), raw, len(raw))
                    k32.GlobalUnlock(dib_mem)
            except Exception:
                dib_mem = None
        ok = False
        for _ in range(6):
            if u32.OpenClipboard(None):
                ok = True
                break
            time.sleep(0.05)
        if not ok:
            return False, "剪贴板被别的程序占着"
        try:
            u32.EmptyClipboard()
            u32.SetClipboardData(CF_HDROP, ctypes.c_void_p(hdrop))
            if dib_mem:
                u32.SetClipboardData(8, ctypes.c_void_p(dib_mem))   # 8 = CF_DIB
        finally:
            u32.CloseClipboard()
        return True, ("已复制到剪贴板(文件和图片格式都在,直接 Ctrl+V 就能粘)"
                      if dib_mem else "已复制到剪贴板,可以直接 Ctrl+V 粘贴")
    except Exception as exc:
        return False, f"复制失败:{exc}"
def window_chrome() -> int:
    """标题栏 + 上下边框占掉的高度。"""
    try:
        import ctypes
        u = ctypes.windll.user32
        return u.GetSystemMetrics(4) + 2 * u.GetSystemMetrics(32)
    except Exception:
        return 48
def short_path(p, limit=34):
    s = str(p)
    if len(s) <= limit:
        return s
    parts = s.split("\\")
    if len(parts) >= 3:
        return parts[0] + "\\…\\" + "\\".join(parts[-2:])
    return s[:limit - 1] + "…"
# ---------------------------------------------------------------- 主界面
class App:
    def __init__(self, state):
        self.state = state
        self.root = tk.Tk()
        self.root.title(f"{core.APP_NAME} · 手机扫码传照片/视频/文件")
        self.root.configure(bg="#070b16")
        self.root.minsize(940, 560)
        self.device_screen = (self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        try:
            self.S = max(1.0, min(2.0, self.root.winfo_fpixels("1i") / 96.0))
        except Exception:
            self.S = 1.0
        # 按屏幕可用区域(扣掉任务栏和标题栏)来定尺寸,保证底部那排按钮一定在屏幕里
        wa = work_area()
        if wa:
            wx, wy, ww, wh = wa
        else:
            wx, wy = 0, 0
            ww, wh = self.device_screen
        chrome = window_chrome()
        W = int(min(826 * self.S, ww - int(16 * self.S)))
        H = int(min(430 * self.S, wh - chrome - int(16 * self.S)))
        x = wx + max(0, (ww - W) // 2)
        free = wh - H - chrome
        y = wy + chrome + (max(0, free // 2))
        self.root.geometry(f"{W}x{H}+{x}+{y}")
        if core.ICON_FILE.exists():
            try:
                self.root.iconbitmap(str(core.ICON_FILE))
            except Exception:
                pass
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except Exception:
            pass
        self.style.configure("Glass.TCombobox", fieldbackground="#1a2237", background="#1a2237",
                             foreground=FG, arrowcolor=DIM, bordercolor="#273250",
                             lightcolor="#273250", darkcolor="#273250", selectbackground="#1a2237",
                             selectforeground=FG, padding=3)
        self.style.map("Glass.TCombobox",
                       fieldbackground=[("readonly", "#1a2237")],
                       foreground=[("readonly", FG)])
        self.canvas = tk.Canvas(self.root, bg="#070b16", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.recs: list[dict] = []
        self.scroll = 0
        self.sel = -1
        self.hover_row = -1
        self.hover_btn = None
        self.xfers: dict[str, dict] = {}
        self.buttons: dict[str, dict] = {}
        self.regions: dict[str, tuple] = {}
        self.photos: dict[str, object] = {}
        self.thumb_key = None
        self.status = "服务运行中"
        self.pulse = 0
        self._resize_job = None
        self.qr_cache = (None, None)
        self.photo_src: dict[str, object] = {}
        self.root.report_callback_exception = self.on_tk_error
        self.bind()
        self.layout()
        self.root.after(120, self.pump)
        self.root.after(900, self.pulse_tick)
    # ---------------- 基础 ----------------
    def on_tk_error(self, exc, val, tb) -> None:
        """回调里出错一定要留痕:pythonw 没有控制台,不然就是"点了没反应"。"""
        text = "".join(traceback.format_exception(exc, val, tb))
        try:
            with open(core.HERE / "错误.log", "a", encoding="utf-8") as fh:
                fh.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}]\n{text}\n")
        except Exception:
            pass
        try:
            messagebox.showerror(core.APP_NAME, f"出了点问题(已记录到 错误.log):\n\n{val}")
        except Exception:
            pass
    def font(self, size, bold=False):
        return (UI_FONT, size, "bold") if bold else (UI_FONT, size)
    def photo_of(self, pil_img, key):
        cached = self.photos.get(key)
        if cached is not None and self.photo_src.get(key) is pil_img:
            return cached
        photo = ImageTk.PhotoImage(pil_img)
        self.photos[key] = photo
        self.photo_src[key] = pil_img
        return photo
    def txt(self, x, y, text, size=9, color=FG, anchor="nw", bold=False, width=0, tag="static"):
        return self.canvas.create_text(x, y, text=text, fill=color, anchor=anchor,
                                       font=self.font(size, bold), width=width, tags=tag)
    def put(self, pil_img, x, y, tag="static", anchor="nw"):
        if pil_img is None:
            return None
        key = f"{tag}:{x}:{y}:{id(pil_img)}"
        photo = self.photos.get(key)
        if photo is None:
            photo = ImageTk.PhotoImage(pil_img)
            self.photos[key] = photo
        if anchor == "nw":
            return self.canvas.create_image(x, y, image=photo, anchor="nw", tags=tag)
        return self.canvas.create_image(x, y, image=photo, anchor=anchor, tags=tag)
    def region(self, key, x0, y0, x1, y1, cb):
        self.regions[key] = (x0, y0, x1, y1, cb)
    def clear_regions(self, prefix):
        for k in [k for k in list(self.regions) if k.startswith(prefix)]:
            self.regions.pop(k, None)
        for k in [k for k in list(self.buttons) if k.startswith(prefix)]:
            self.buttons.pop(k, None)
    # ---------------- 布局 ----------------
    def layout(self):
        c = self.canvas
        c.delete("all")
        self.buttons.clear()
        self.regions.clear()
        S = self.S
        W = max(700, c.winfo_width())
        H = max(420, c.winfo_height())
        self.W, self.H = W, H
        pad = int(14 * S)
        head_h = int(38 * S)
        status_h = int(50 * S)
        gap = int(10 * S)
        col_a = int(236 * S)
        col_c = int(198 * S)
        self.put(grad_round(W, H, 0, BG_TOP, BG_BOT), 0, 0, tag="bg")
        # ---- 顶部 ----
        icon_px = int(34 * S)
        if core.ICON_FILE.with_name("icon.png").exists():
            try:
                self.brand_img = Image.open(core.ICON_FILE.with_name("icon.png")).resize(
                    (icon_px, icon_px), Image.LANCZOS)
                self.put(self.brand_img, pad, pad, tag="static")
            except Exception:
                pass
        tx = pad + icon_px + int(12 * S)
        self.txt(tx, pad + int(1 * S), core.APP_NAME, 13, FG, bold=True)
        self.txt(tx, pad + int(22 * S), "局域网直传 · 不经过外网 · 支持断点续传", 8, DIM)
        self.pill_x0 = None
        # ---- 三大列 ----
        cy0 = pad + head_h + int(4 * S)
        ch = H - cy0 - status_h - pad
        ax = pad
        cx = W - pad - col_c
        bx = ax + col_a + gap
        bw = cx - gap - bx
        self.geo = {"pad": pad, "gap": gap, "cy0": cy0, "ch": ch, "ax": ax, "bx": bx, "cx": cx,
                    "aw": col_a, "bw": bw, "cw": col_c, "status_h": status_h}
        self.draw_left()
        self.draw_center()
        self.draw_preview()
        self.draw_status()
        self.draw_rows()
        self.draw_xfers()
        self.draw_pill()
    def draw_pill(self):
        g = self.geo
        S = self.S
        text = "●  服务运行中"
        w = int(len("服务运行中") * 13 * S + 34 * S)
        x1 = self.W - g["pad"]
        x0 = x1 - w
        y0 = g["pad"] + int(3 * S)
        h = int(26 * S)
        self.pill_y0, self.pill_x1 = y0, x1
        self.put(flat_round(w, h, h // 2, (52, 211, 153, 22), outline=(52, 211, 153, 70)),
                 x0, y0, tag="pill")
        self.put(flat_round(int(7 * S), int(7 * S), int(3.5 * S), (52, 211, 153, 255)),
                 x0 + int(13 * S), y0 + int(9.5 * S), tag="pill")
        self.txt(x0 + int(26 * S), y0 + int(6 * S), "服务运行中", 8, "#a7f3d0")
    def draw_left(self):
        g = self.geo
        S = self.S
        pad, gap = g["pad"], g["gap"]
        ax, cy0, ch, aw = g["ax"], g["cy0"], g["ch"], g["aw"]
        # 先把下面几块的高度算清楚,剩下的全给二维码
        url_h = int(28 * S)
        addr_h = int(30 * S)
        steps_h = int(20 * S)
        note_h = int(18 * S)
        gaps = int(9 * S) * 3
        fixed = url_h + addr_h + steps_h + note_h + gaps
        qr_card_h = int(max(160 * S, ch - fixed))
        radius = int(18 * S)
        sh = shadow_box(aw, qr_card_h, radius, int(9 * S), 110, int(5 * S))
        self.put(sh, ax - int(9 * S) * 2, cy0 - int(9 * S) * 2 - int(5 * S), tag="static")
        self.put(flat_round(aw, qr_card_h, radius, (255, 255, 255, 255)), ax, cy0)
        qr_px = qr_card_h - int(30 * S)
        self.draw_qr(ax + (aw - qr_px) // 2, cy0 + int(15 * S), qr_px)
        y = cy0 + qr_card_h + int(9 * S)
        # 网址栏
        fh = url_h
        self.put(flat_round(aw - int(52 * S), fh, int(9 * S), (255, 255, 255, 14),
                            outline=(*LINE, 255)), ax, y)
        self.url_text_id = self.txt(ax + int(10 * S), y + fh / 2, "", 8, "#9db8ff", anchor="w")
        self.set_url()
        bw = int(46 * S)
        self.add_button("copy", ax + aw - bw, y, bw, fh, "复制", "ghost", self.copy_link)
        y += fh + int(9 * S)
        # 地址
        self.txt(ax, y + int(3 * S), "电脑地址", 8, DIM)
        ips = core.local_ipv4_candidates()
        if not hasattr(self, "ip_var"):
            self.ip_var = tk.StringVar(value=self.state.host if self.state.host in ips else ips[0])
            self.ip_box = ttk.Combobox(self.root, textvariable=self.ip_var, values=ips, width=13,
                                       state="readonly", style="Glass.TCombobox",
                                       font=self.font(8))
            self.ip_box.bind("<<ComboboxSelected>>", self.change_ip)
        self.canvas.create_window(ax + int(66 * S), y, window=self.ip_box, anchor="nw",
                                  width=int(96 * S), height=int(24 * S), tags="static")
        y += addr_h
        self.txt(ax, y + int(3 * S), "手机扫码 → 选文件 → 自动存进下面这个文件夹", 8, FAINT,
                 width=aw)
        y += steps_h
        if self.state.codex_sync:
            same = Path(self.state.codex_dir).resolve() == Path(self.state.inbox).resolve()
            note = "文件就在 Codex 文件夹里,直接可读" if same else "会同时放一份到 Codex 文件夹"
        else:
            note = "只存进收件箱(同步到 Codex 已关)"
        self.txt(ax, y + int(2 * S), note, 8, FAINT, width=aw)
    def draw_qr(self, x, y, px):
        self.canvas.delete("qr")
        key = (self.state.url, px)
        if PIL_OK and self.qr_cache[0] == key:
            img = self.qr_cache[1]
        else:
            img = None
            try:
                import qrcode
                q = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
                q.add_data(self.state.url)
                q.make(fit=True)
                img = q.make_image(fill_color="#0b0f1c", back_color="white").convert("RGB")
                img = img.resize((px, px), Image.NEAREST)
            except Exception:
                img = None
            self.qr_cache = (key, img)
        if img is not None:
            self.put(img, x, y, tag="qr")
        else:
            self.txt(x, y, "未安装 qrcode\n请手动输入网址", 9, "#333", tag="qr", width=px)
    def add_button(self, key, x, y, w, h, text, style, cb):
        fpx = max(11, int(9 * self.S))
        imgs = {}
        for st in ("normal", "hover", "press"):
            pil = button_image(text, int(w), int(h), style, st, fpx)
            imgs[st] = self.photo_of(pil, f"btnimg:{key}:{st}:{int(w)}x{int(h)}")
        item = self.canvas.create_image(x, y, image=imgs["normal"], anchor="nw", tags="btn")
        self.buttons[key] = {"item": item, "imgs": imgs, "state": "normal"}
        self.region(key, x, y, x + w, y + h, cb)
        return item
    def draw_center(self):
        g = self.geo
        S = self.S
        bx, cy0, ch, bw = g["bx"], g["cy0"], g["ch"], g["bw"]
        n, total = self.state.totals()
        xfer_count = len(self.xfers)
        self.txt(bx, cy0 + int(2 * S), "正在接收", 11, FG, bold=True)
        if xfer_count:
            shown = min(xfer_count, 2)
            self.txt(bx + int(86 * S), cy0 + int(6 * S), f"{xfer_count} 个文件正在传", 8,
                     "#8fb2ff")
            xfer_h = int(22 * S) + shown * int(50 * S)
        else:
            self.txt(bx + int(86 * S), cy0 + int(6 * S), "手机还没开始传", 8, FAINT)
            xfer_h = int(24 * S)
        xfer_h = min(xfer_h, int(ch * 0.5))
        self.xfer_rect = (bx, cy0, bx + bw, cy0 + xfer_h)
        ly = cy0 + xfer_h + int(6 * S)
        self.txt(bx, ly, f"已收到 {n} 个文件", 11, FG, bold=True)
        if n:
            self.txt(bx + int(112 * S), ly + int(4 * S), f"共 {core.human_size(total)}", 8, DIM)
        card_y = ly + int(24 * S)
        card_h = max(int(60 * S), cy0 + ch - card_y)
        self.put(grad_round(bw, card_h, int(16 * S), CARD_TOP, CARD_BOT, outline=(*LINE, 255)),
                 bx, card_y, tag="static")
        self.list_rect = (bx, card_y, bx + bw, card_y + card_h)
        self.rows_y0 = card_y + int(7 * S)
        self.row_h = int(30 * S)
        self.rows_h = card_h - int(14 * S)
        self.rows_visible = max(1, self.rows_h // self.row_h)
    def draw_rows(self):
        self.canvas.delete("rows")
        if not hasattr(self, "list_rect"):
            return
        g = self.geo
        S = self.S
        bx = self.list_rect[0]
        bw = self.list_rect[2] - self.list_rect[0]
        padx = int(9 * S)
        name_x = bx + padx + int(34 * S)
        for vis in range(self.rows_visible):
            idx = self.scroll + vis
            if idx >= len(self.recs):
                break
            rec = self.recs[idx]
            y = self.rows_y0 + vis * self.row_h
            selected = idx == self.sel
            hovered = idx == self.hover_row
            if selected:
                bg = grad_round(bw - padx * 2, self.row_h - int(4 * S), int(10 * S),
                                (44, 66, 116), (37, 55, 98), outline=(120, 160, 255, 90))
            elif hovered:
                bg = flat_round(bw - padx * 2, self.row_h - int(4 * S), int(10 * S),
                                (255, 255, 255, 16))
            else:
                bg = flat_round(bw - padx * 2, self.row_h - int(4 * S), int(10 * S),
                                (255, 255, 255, 6))
            self.put(bg, bx + padx, y + int(2 * S), tag="rows")
            bpx = int(21 * S)
            self.put(badge_image(rec.get("kind", "其他"), bpx), bx + padx + int(7 * S),
                     y + (self.row_h - bpx) // 2, tag="rows")
            self.txt(name_x, y + int(4 * S), ellipsis(rec["name"], 30), 9,
                     FG if (selected or hovered) else FG2)
            self.txt(name_x, y + int(18 * S),
                     f"{rec['kind']} · {core.human_size(rec['size'])} · {rec['time'][11:]}", 8, DIM)
            self.txt(bx + bw - padx - int(7 * S), y + self.row_h // 2, rec["time"][:10], 8,
                     FAINT, anchor="e")
    def draw_xfers(self):
        self.canvas.delete("xfer")
        if not hasattr(self, "xfer_rect"):
            return
        S = self.S
        bx, by, bx1, by1 = self.xfer_rect
        bw = bx1 - bx
        if not self.xfers:
            return
        row_h = int(46 * S)
        for i, (uid, info) in enumerate(list(self.xfers.items())):
            y = by + int(22 * S) + i * int(50 * S)
            if y + row_h > by1:
                break
            self.put(grad_round(bw, row_h, int(13 * S), CARD2_TOP, CARD2_BOT,
                                outline=(*LINE_SOFT, 255)), bx, y, tag="xfer")
            bpx = int(24 * S)
            self.put(badge_image(core.kind_group(info["name"]), bpx), bx + int(11 * S),
                     y + int(11 * S), tag="xfer")
            self.txt(bx + int(44 * S), y + int(9 * S), ellipsis(info["name"], 26), 9, FG)
            pct = (info["done"] / info["size"]) if info["size"] else 0
            meta = f"{core.human_size(info['done'])} / {core.human_size(info['size'])}"
            if info.get("speed", 0) > 0:
                meta += f" · {core.human_size(info['speed'])}/s"
            self.txt(bx + bw - int(12 * S), y + int(11 * S), meta, 8, DIM, anchor="ne")
            bar_w = bw - int(24 * S)
            bar_h = int(6 * S)
            byy = y + int(30 * S)
            self.put(bar_track(bar_w, bar_h), bx + int(12 * S), byy, tag="xfer")
            fill = bar_fill(bar_w, bar_h, pct, A1, OK_C)
            if fill is not None:
                self.put(fill, bx + int(12 * S), byy, tag="xfer")
            info["_pct"] = pct
    def draw_preview(self):
        self.canvas.delete("prev")
        self.clear_regions("prev_")
        g = self.geo
        S = self.S
        cx, cy0, ch, cw = g["cx"], g["cy0"], g["ch"], g["cw"]
        self.put(grad_round(cw, ch, int(16 * S), CARD_TOP, CARD_BOT, outline=(*LINE, 255)),
                 cx, cy0, tag="prev")
        self.txt(cx + int(14 * S), cy0 + int(12 * S), "预览", 8, DIM, tag="prev")
        thumb_h = int(min(120 * S, ch * 0.26))
        tx, ty = cx + int(14 * S), cy0 + int(30 * S)
        tw = cw - int(28 * S)
        self.thumb_rect = (tx, ty, tx + tw, ty + thumb_h)
        rec = self.recs[self.sel] if 0 <= self.sel < len(self.recs) else None
        if rec is None:
            self.put(flat_round(tw, thumb_h, int(12 * S), (255, 255, 255, 10),
                                outline=(*LINE_SOFT, 255)), tx, ty, tag="prev")
            self.txt(tx + tw / 2, ty + thumb_h / 2, "选中一个文件", 9, FAINT, anchor="center",
                     tag="prev")
            self.txt(cx + int(14 * S), ty + thumb_h + int(14 * S), "还没有收到文件", 10, FG2,
                     bold=True, width=tw, tag="prev")
            self.txt(cx + int(14 * S), ty + thumb_h + int(38 * S),
                     "手机传过来的照片、视频、文件\n会出现在这里", 9, DIM, width=tw, tag="prev")
            return
        kind = rec.get("kind", "其他")
        shown = False
        if PIL_OK and kind == "图片":
            key = f"thumb:{rec['path']}:{int(tw)}x{int(thumb_h)}"
            if self.thumb_key and self.thumb_key != key:
                self.photos.pop(self.thumb_key, None)
            img = self.photos.get(key)
            if img is None:
                try:
                    im = Image.open(rec["path"])
                    im = ImageOps.exif_transpose(im)
                    im.thumbnail((tw, thumb_h), Image.LANCZOS)
                    img = ImageTk.PhotoImage(im)
                    self.photos[key] = img
                    self.thumb_key = key
                except Exception:
                    img = None
            if img is not None:
                self.put(flat_round(tw, thumb_h, int(12 * S), (255, 255, 255, 12),
                                    outline=(*LINE_SOFT, 255)), tx, ty, tag="prev")
                self.canvas.create_image(tx + tw / 2, ty + thumb_h / 2, image=img, tags="prev")
                shown = True
        if not shown:
            self.put(flat_round(tw, thumb_h, int(12 * S), (255, 255, 255, 10),
                                outline=(*LINE_SOFT, 255)), tx, ty, tag="prev")
            bpx = int(46 * S)
            self.put(badge_image(kind, bpx), tx + (tw - bpx) // 2, ty + (thumb_h - bpx) // 2,
                     tag="prev")
        bh = int(25 * S)
        btn_gap = int(6 * S)
        btns_top = cy0 + ch - int(12 * S) - (4 * bh + 3 * btn_gap)
        ny = ty + thumb_h + int(12 * S)
        self.txt(cx + int(14 * S), ny, rec["name"], 10, FG, bold=True, width=tw, tag="prev")
        dy = ny + int(30 * S)
        avail = btns_top - dy - int(6 * S)
        max_lines = max(1, avail // int(13 * S))
        lines = [f"{kind} · {core.human_size(rec['size'])}",
                 f"收到 {rec['time']}",
                 f"来自 {rec.get('client', '手机')}",
                 f"{rec.get('rel', rec['name'])}"]
        if len(lines) > max_lines:
            lines = lines[:max_lines]
        self.txt(cx + int(14 * S), dy, "\n".join(lines), 8, DIM, width=tw, tag="prev")
        by = btns_top
        self.add_button("prev_open", cx + int(14 * S), by, tw, bh, "打开文件", "primary",
                        self.open_file)
        by += bh + btn_gap
        self.add_button("prev_clip", cx + int(14 * S), by, tw, bh, "复制到剪贴板", "ghost",
                        self.copy_clip)
        by += bh + btn_gap
        self.add_button("prev_reveal", cx + int(14 * S), by, tw, bh, "在资源管理器里定位", "ghost",
                        self.reveal)
        by += bh + btn_gap
        self.add_button("prev_move", cx + int(14 * S), by, tw, bh, "复制 / 移动到别处", "ghost",
                        lambda: self.move_copy(None))
    def draw_switch(self, x, y, on, w, h):
        S = self.S
        self.put(flat_round(w, h, h // 2, (52, 211, 153, 205) if on else (255, 255, 255, 26),
                            outline=(52, 211, 153, 235) if on else (*LINE, 255)),
                 x, y, tag="status")
        k = h - int(5 * S)
        kx = x + (w - k - int(3.5 * S) if on else int(3.5 * S))
        self.put(flat_round(k, k, k // 2, (255, 255, 255, 240)), kx, y + int(2.5 * S),
                 tag="status")
    def draw_status(self):
        c = self.canvas
        c.delete("status")
        c.delete("bar_text")
        S = self.S
        g = self.geo
        bar_h = g["status_h"]
        y = self.H - bar_h
        c.create_rectangle(0, y, self.W, y + 1, fill="#1b2233", outline="", tags="status")
        pad = g["pad"]
        # ---- 右边:自动同步到 Codex ----
        narrow = self.W < int(1120 * self.S / 1.5)
        sw_w, sw_h = int(30 * S), int(16 * S)
        lbl = "Codex 同步" if narrow else "自动同步到 Codex"
        lbl_w = int((92 if narrow else 150) * S)
        stats_w = 0 if narrow else int(150 * S)
        sw_x = self.W - pad - stats_w - int(12 * S) - lbl_w - int(14 * S) - sw_w
        sw_y = y + (bar_h - sw_h) // 2
        self.draw_switch(sw_x, sw_y, bool(self.state.codex_sync), sw_w, sw_h)
        self.txt(sw_x + sw_w + int(12 * S), y + int(9 * S), lbl, 8,
                 FG2 if self.state.codex_sync else FAINT, tag="status")
        self.region("sync_sw", sw_x - int(5 * S), sw_y - int(5 * S), sw_x + sw_w + int(5 * S),
                    sw_y + sw_h + int(5 * S), self.toggle_sync)
        self.region("sync_lbl", sw_x + sw_w + int(8 * S), sw_y - int(4 * S),
                    sw_x + sw_w + int(8 * S) + lbl_w, sw_y + sw_h + int(4 * S),
                    self.open_codex_dir)
        if stats_w:
            n, total = self.state.totals()
            self.txt(self.W - pad, y + int(8 * S),
                     f"已收 {n} 个 · {core.human_size(total)}", 8, FAINT, anchor="ne", tag="status")
        # ---- 左边:收件箱 ----
        self.txt(pad, y + int(8 * S), "收件箱", 8, DIM, tag="status")
        self.txt(pad + int(48 * S), y + int(7 * S), short_path(self.state.inbox, 30), 9, FG2,
                 tag="status")
        self.region("inbox_path", pad + int(44 * S), y + int(2 * S),
                    pad + int(48 * S) + int(198 * S), y + int(26 * S), self.copy_inbox_path)
        bh = int(28 * S)
        by = y + (bar_h - bh) // 2
        bx = pad + int(48 * S) + int(200 * S)
        self.add_button("opendir", bx, by, int(94 * S), bh, "打开文件夹", "ghost", self.open_inbox)
        self.add_button("changedir", bx + int(100 * S), by, int(94 * S), bh, "换个文件夹", "ghost",
                        self.change_dir)
        self.draw_bar_text()
    def draw_bar_text(self):
        g = self.geo
        S = self.S
        y = self.H - g["status_h"]
        n, total = self.state.totals()
        self.txt(self.W - g["pad"], y + int(8 * S),
                 f"已收 {n} 个 · {core.human_size(total)}", 8, FAINT, anchor="ne", tag="bar_text")
        msg = self.status
        if len(msg) > 46:
            msg = msg[:45] + "…"
        self.txt(g["pad"], y + int(28 * S), msg, 8, "#6f7d99", tag="bar_text")
    # ---------------- 数据联动 ----------------
    def set_url(self):
        url = self.state.url
        shown = url.replace("http://", "")
        if len(shown) > 32:
            shown = shown[:20] + "…" + shown[-9:]
        if hasattr(self, "url_text_id"):
            self.canvas.itemconfigure(self.url_text_id, text=shown)
    def change_ip(self, *_):
        self.state.host = self.ip_var.get()
        self.set_url()
        self.qr_cache = (None, None)
        self.layout()
    def pump(self):
        try:
            while True:
                ev = self.state.events.get_nowait()
                kind = ev.get("type")
                if kind == "send_start":
                    uid = ev["id"]
                    self.xfers[uid] = {"name": ev["name"], "size": ev["size"],
                                       "done": ev.get("done", 0), "t": time.time(),
                                       "last": ev.get("done", 0), "speed": 0.0}
                    self.layout()          # 传输区高度变了,整列都要重排
                elif kind == "send_progress":
                    uid = ev["id"]
                    info = self.xfers.get(uid)
                    if info is None:
                        info = self.xfers[uid] = {"name": ev["name"], "size": ev["size"],
                                                  "done": 0, "t": time.time(), "last": 0,
                                                  "speed": 0.0}
                        self.layout()
                    now = time.time()
                    dt = now - info["t"]
                    if dt >= 0.25:
                        inst = max(0.0, (ev["done"] - info["last"]) / dt)
                        info["speed"] = inst if info["speed"] <= 0 else info["speed"] * 0.55 + inst * 0.45
                        info["t"], info["last"] = now, ev["done"]
                    info["done"] = ev["done"]
                    self.draw_xfers()
                elif kind == "send_done":
                    self.xfers.pop(ev["id"], None)
                    self.layout()
                elif kind == "received":
                    self.recs.insert(0, ev["rec"])
                    self.sel, self.scroll = 0, 0
                    self.layout()
                    try:
                        self.root.bell()
                    except Exception:
                        pass
                    self.flash(f"刚收到:{ev['rec']['name']}")
                elif kind == "log":
                    self.flash(ev["msg"])
                elif kind == "synced":
                    self.flash(f"已同步到 Codex:{ev['name']}")
        except queue.Empty:
            pass
        self.root.after(180, self.pump)
    def pulse_tick(self):
        self.pulse = (self.pulse + 1) % 2
        now = time.time()
        stale = [u for u, i in self.xfers.items() if now - i["t"] > 45]
        if stale:
            for uid in stale:
                self.xfers.pop(uid, None)
            self.layout()
        self.root.after(1500, self.pulse_tick)
    def flash(self, msg):
        self.status = msg
        self.canvas.delete("bar_text")
        self.draw_bar_text()
        self.root.after(4000, self.restore_status)
    def restore_status(self):
        if self.status.startswith("刚收到") or self.status.startswith("接着"):
            self.status = "服务运行中"
            self.canvas.delete("bar_text")
            self.draw_bar_text()
    # ---------------- 交互 ----------------
    def bind(self):
        c = self.canvas
        c.bind("<Motion>", self.on_motion)
        c.bind("<Leave>", lambda e: self.set_hover(None, -1))
        c.bind("<Button-1>", self.on_click)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<Button-3>", self.on_right)
        c.bind("<MouseWheel>", self.on_wheel)
        self.root.bind("<Configure>", self.on_resize)
        self.root.bind("<Up>", lambda e: self.move_sel(-1))
        self.root.bind("<Down>", lambda e: self.move_sel(1))
        self.root.bind("<Return>", lambda e: self.open_file())
        self.root.bind("<Escape>", lambda e: self.root.destroy())
    def row_at(self, x, y):
        if not hasattr(self, "list_rect"):
            return None
        x0, y0, x1, y1 = self.list_rect
        if not (x0 <= x <= x1 and self.rows_y0 <= y <= y1):
            return None
        idx = self.scroll + (y - self.rows_y0) // self.row_h
        return idx if 0 <= idx < len(self.recs) else None
    def btn_at(self, x, y):
        for key, (x0, y0, x1, y1, cb) in self.regions.items():
            if x0 <= x <= x1 and y0 <= y <= y1:
                return key, cb
        return None, None
    def set_hover(self, btn_key, row_idx):
        changed = False
        if btn_key != self.hover_btn:
            if self.hover_btn and self.hover_btn in self.buttons:
                b = self.buttons[self.hover_btn]
                b["state"] = "normal"
                self.canvas.itemconfigure(b["item"], image=b["imgs"]["normal"])
            self.hover_btn = btn_key
            if btn_key and btn_key in self.buttons:
                b = self.buttons[btn_key]
                b["state"] = "hover"
                self.canvas.itemconfigure(b["item"], image=b["imgs"]["hover"])
            changed = True
        if row_idx != self.hover_row:
            self.hover_row = row_idx
            changed = True
        if changed and row_idx != self.sel:
            self.draw_rows()
    def on_motion(self, e):
        key, _ = self.btn_at(e.x, e.y)
        idx = self.row_at(e.x, e.y)
        self.set_hover(key, idx if idx is not None else -1)
        self.canvas.configure(cursor="hand2" if (key or idx is not None) else "")
    def on_click(self, e):
        key, cb = self.btn_at(e.x, e.y)
        if key and key in self.buttons:
            b = self.buttons[key]
            self.canvas.itemconfigure(b["item"], image=b["imgs"]["press"])
            self.root.after(90, lambda: self.canvas.itemconfigure(b["item"],
                                                                  image=b["imgs"]["hover"]))
        idx = self.row_at(e.x, e.y)
        if idx is not None:
            self.sel = idx
            self.draw_rows()
            self.draw_preview()
            return
        if cb:
            cb()
    def on_double(self, e):
        idx = self.row_at(e.x, e.y)
        if idx is not None:
            self.sel = idx
            self.open_file()
    def on_right(self, e):
        key, _ = self.btn_at(e.x, e.y)
        if key == "sync_sw":
            menu = tk.Menu(self.root, tearoff=0, bg="#151d2e", fg=FG, activebackground="#2b4a8f",
                           activeforeground="#ffffff", bd=0, font=self.font(9))
            menu.add_command(label="打开 Codex 文件夹", command=self.open_codex_dir)
            menu.add_command(label="换一个 Codex 文件夹…", command=self.pick_codex_dir)
            menu.add_separator()
            menu.add_command(label="关闭同步" if self.state.codex_sync else "开启同步",
                             command=self.toggle_sync)
            try:
                menu.tk_popup(e.x_root, e.y_root)
            finally:
                menu.grab_release()
            return
        idx = self.row_at(e.x, e.y)
        if idx is None:
            return
        self.sel = idx
        self.draw_rows()
        self.draw_preview()
        menu = tk.Menu(self.root, tearoff=0, bg="#151d2e", fg=FG, activebackground="#2b4a8f",
                       activeforeground="#ffffff", bd=0, font=self.font(9))
        menu.add_command(label="打开", command=self.open_file)
        menu.add_command(label="在资源管理器里定位", command=self.reveal)
        menu.add_separator()
        menu.add_command(label="复制文件路径", command=self.copy_path)
        menu.add_command(label="复制到剪贴板(可直接粘贴)", command=self.copy_clip)
        menu.add_command(label="复制到…", command=lambda: self.move_copy(False))
        menu.add_command(label="移动到…", command=lambda: self.move_copy(True))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()
    def on_wheel(self, e):
        if not hasattr(self, "list_rect"):
            return
        x0, y0, x1, y1 = self.list_rect
        if not (x0 <= e.x <= x1 and y0 <= e.y <= y1):
            return
        step = -1 if e.delta > 0 else 1
        self.scroll_rows(step)
    def scroll_rows(self, step):
        limit = max(0, len(self.recs) - self.rows_visible)
        new = max(0, min(limit, self.scroll + step))
        if new != self.scroll:
            self.scroll = new
            self.draw_rows()
    def move_sel(self, step):
        if not self.recs:
            return
        self.sel = max(0, min(len(self.recs) - 1, (self.sel if self.sel >= 0 else 0) + step))
        if self.sel < self.scroll:
            self.scroll = self.sel
        elif self.sel >= self.scroll + self.rows_visible:
            self.scroll = self.sel - self.rows_visible + 1
        self.draw_rows()
        self.draw_preview()
    def on_resize(self, e):
        if e.widget is not self.root:
            return
        if self._resize_job:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(140, self.relayout)
    def relayout(self):
        self._resize_job = None
        self.scroll = max(0, min(self.scroll, max(0, len(self.recs) - self.rows_visible)))
        self.layout()
    # ---------------- 动作 ----------------
    def cur(self):
        return self.recs[self.sel] if 0 <= self.sel < len(self.recs) else None
    def copy_link(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.state.url)
        self.flash("链接已复制到剪贴板")
    def copy_inbox_path(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(str(self.state.inbox))
        self.flash("文件夹路径已复制,可以直接贴给 AI 让它去读")
    def toggle_sync(self):
        self.state.codex_sync = not bool(self.state.codex_sync)
        core.save_settings()
        self.layout()
        self.flash("已开启:收到的文件会放一份到 Codex 文件夹"
                   if self.state.codex_sync else "已关闭:只存进收件箱")
    def pick_codex_dir(self):
        p = filedialog.askdirectory(title="Codex 文件夹放在哪",
                                    initialdir=str(self.state.codex_dir))
        if not p:
            return
        self.state.codex_dir = Path(p)
        self.state.codex_sync = True
        core.save_settings()
        self.layout()
        self.flash("Codex 文件夹已设为:" + str(self.state.codex_dir))
    def open_codex_dir(self):
        d = Path(self.state.codex_dir)
        try:
            d.mkdir(parents=True, exist_ok=True)
            os.startfile(str(d))                     # noqa: S606
        except Exception as exc:
            messagebox.showerror(core.APP_NAME, str(exc))
    def open_inbox(self):
        self.state.ensure_dirs()
        try:
            os.startfile(str(self.state.inbox))     # noqa: S606
        except Exception as exc:
            messagebox.showerror(core.APP_NAME, str(exc))
    def change_dir(self):
        p = filedialog.askdirectory(title="选一个新的收件箱文件夹",
                                    initialdir=str(self.state.inbox))
        if not p:
            return
        new = Path(p)
        try:
            new.mkdir(parents=True, exist_ok=True)
            (new / ".parts").mkdir(exist_ok=True)
        except Exception as exc:
            messagebox.showerror(core.APP_NAME, f"这个目录不能用:{exc}")
            return
        self.state.inbox = new
        self.state.parts_dir = new / ".parts"
        core.save_settings()
        self.flash("收件箱已切换")
        self.layout()
    def open_file(self):
        rec = self.cur()
        if not rec:
            self.flash("先在列表里选一个文件")
            return
        try:
            os.startfile(rec["path"])               # noqa: S606
        except Exception as exc:
            messagebox.showerror(core.APP_NAME, str(exc))
    def reveal(self):
        rec = self.cur()
        if not rec:
            return
        try:
            subprocess.Popen(["explorer", "/select,", rec["path"]])
        except Exception as exc:
            messagebox.showerror(core.APP_NAME, str(exc))
    def copy_path(self):
        rec = self.cur()
        if rec:
            self.root.clipboard_clear()
            self.root.clipboard_append(rec["path"])
            self.flash("路径已复制")
    def copy_clip(self):
        rec = self.cur()
        if not rec:
            self.flash("先在列表里选一个文件")
            return
        p = Path(rec["path"])
        if not p.exists():
            self.flash("文件不在了(可能被移走或删了)")
            return
        ok, msg = copy_to_clipboard(p)
        self.flash(msg if ok else msg)
    def move_copy(self, move):
        rec = self.cur()
        if not rec:
            self.flash("先在列表里选一个文件")
            return
        src = Path(rec["path"])
        target = filedialog.askdirectory(title="放到哪个文件夹", initialdir=str(self.state.inbox))
        if not target:
            return
        if move is None:
            ans = messagebox.askyesnocancel(
                core.APP_NAME, "是 = 移动(原位置不再保留)\n否 = 复制一份\n取消 = 算了")
            if ans is None:
                return
            move = bool(ans)
        dst = core.unique_path(Path(target) / src.name)
        try:
            if move:
                shutil.move(str(src), str(dst))
            else:
                shutil.copy2(str(src), str(dst))
        except Exception as exc:
            messagebox.showerror(core.APP_NAME, f"操作失败:{exc}")
            return
        self.flash(("已移动 → " if move else "已复制 → ") + str(dst))
    def mainloop(self):
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self.root.mainloop()
def run(state) -> None:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App(state).mainloop()
