"""系統匣小工具（Windows）：右下角圖示，點開看狀態、複製網址、改顯示名稱、開機自動啟動、重新啟動、結束（需確認）。"""
import ctypes
import json
import os
import socket
import subprocess
import sys
import threading
import time

import pystray
from PIL import Image, ImageDraw, ImageFont

import hostlib
from hostlib import log

TITLE = "茲茲 OCR 主機"
MB_OK, MB_YESNO, MB_ICON_INFO, MB_ICON_WARN, IDYES = 0x0, 0x4, 0x40, 0x30, 6


def msgbox(text, title=TITLE, flags=MB_OK | MB_ICON_INFO):
    return ctypes.windll.user32.MessageBoxW(0, text, title, flags)


def copy_text(text):
    """複製到剪貼簿（Unicode）。"""
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    data = text.encode("utf-16le") + b"\x00\x00"
    h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    ptr = k32.GlobalLock(ctypes.c_void_p(h))
    ctypes.memmove(ptr, data, len(data))
    k32.GlobalUnlock(ctypes.c_void_p(h))
    if u32.OpenClipboard(0):
        u32.EmptyClipboard()
        u32.SetClipboardData(CF_UNICODETEXT, ctypes.c_void_p(h))
        u32.CloseClipboard()


def input_box(prompt, title, default=""):
    """沒有 tkinter：用 PowerShell 內建的 InputBox（隱藏 PowerShell 視窗）。取消回傳 None。"""
    esc = lambda t: t.replace("'", "''")
    cmd = ("Add-Type -AssemblyName Microsoft.VisualBasic;"
           f"[Microsoft.VisualBasic.Interaction]::InputBox('{esc(prompt)}','{esc(title)}','{esc(default)}')")
    r = subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", cmd], capture_output=True,
                       creationflags=hostlib.CREATE_NO_WINDOW, timeout=600)
    out = r.stdout.decode("utf-8", "replace").strip()
    return out or None


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def make_icon(ok):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=(133, 1, 3, 255) if ok else (110, 111, 116, 255))
    font = None
    for name in ("msjh.ttc", "msjhbd.ttc", "mingliu.ttc", "arial.ttf"):
        try:
            font = ImageFont.truetype(name, 38)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    d.text((32, 30), "茲", fill=(255, 255, 255, 255), font=font, anchor="mm")
    d.ellipse((38, 38, 62, 62), fill=(255, 255, 255, 255))
    d.ellipse((42, 42, 58, 58), fill=(46, 204, 113, 255) if ok else (231, 76, 60, 255))
    return img


def autostart_get():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            winreg.QueryValueEx(k, "ZiziOCR")
            return True
    except OSError:
        return False


def autostart_set(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as k:
        if on and getattr(sys, "frozen", False):
            winreg.SetValueEx(k, "ZiziOCR", 0, winreg.REG_SZ, f'"{sys.executable}" --background')
        else:
            try:
                winreg.DeleteValue(k, "ZiziOCR")
            except OSError:
                pass


def run(app, stop):
    cfg = hostlib.load_config()
    # 第一次執行：預設開機自動啟動、問顯示名稱
    if cfg.get("autostart") and not autostart_get():
        try:
            autostart_set(True)
        except OSError as e:
            log(f"設定開機自動啟動失敗：{e}")

    def status():
        ocr, ai = port_open(app.PORT), port_open(app.BRIDGE_PORT)
        reg = hostlib.read_state()
        return ocr, ai, reg

    def status_text():
        ocr, ai, reg = status()
        lines = [f"OCR 主機：{'執行中' if ocr else '沒有在跑'}（本機 {app.PORT}）",
                 f"AI 橋接：{'執行中' if ai else '沒有在跑'}（本機 {app.BRIDGE_PORT}）",
                 f"顯示名稱：{hostlib.display_name()}"]
        if reg.get("ok"):
            lines += [f"Tailscale：{reg.get('dns')}", f"OCR 網址：{reg.get('url')}", f"AI 橋接網址：{reg.get('aiUrl')}",
                      f"網站登錄：已登錄（{max(0, int((time.time() * 1000 - reg.get('at', 0)) / 60000))} 分鐘前更新）"]
        else:
            lines += [f"網站登錄：尚未完成（{reg.get('error', '等待第一次登錄')}）"]
        eng = [v['label'] for v in hostlib.detect_engines().values() if v['ok']]
        lines.append("可用的 AI 工具：" + ("、".join(eng) if eng else "沒有偵測到（AI 分類不能用，OCR 仍可用）"))
        lines.append(f"版本：{app.VERSION}　紀錄：{hostlib.LOG_FILE}")
        return "\n".join(lines)

    def show_status(icon=None, item=None):
        threading.Thread(target=lambda: msgbox(status_text()), daemon=True).start()

    def copy_url(key):
        def f(icon, item):
            reg = hostlib.read_state()
            if reg.get(key):
                copy_text(reg[key])
            else:
                msgbox("還沒取得網址：請確認 Tailscale 已安裝並連線。", flags=MB_OK | MB_ICON_WARN)
        return f

    def set_name(icon=None, item=None):
        def go():
            c = hostlib.load_config()
            n = input_box("這台主機要在網站上顯示什麼名稱？（例如：劍潭；留空就用電腦名稱）", TITLE, hostlib.display_name(c))
            if n is not None:
                c["displayName"] = n.strip()
                hostlib.save_config(c)
                log(f"顯示名稱改為：{hostlib.display_name(c)}")
        threading.Thread(target=go, daemon=True).start()

    def toggle_autostart(icon, item):
        try:
            autostart_set(not autostart_get())
        except OSError as e:
            msgbox(f"設定失敗：{e}", flags=MB_OK | MB_ICON_WARN)

    def open_logs(icon=None, item=None):
        os.startfile(hostlib.APPDIR)

    def restart(icon, item):
        log("使用者要求重新啟動")
        exe = sys.executable if getattr(sys, "frozen", False) else None
        if exe:
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
                              f"Start-Sleep 3; Start-Process -FilePath '{exe}' -ArgumentList '--background'"],
                             creationflags=hostlib.CREATE_NO_WINDOW)
        icon.stop()
        stop.set()
        os._exit(0)

    def quit_app(icon, item):
        if msgbox("結束後，老師就連不到這台主機（OCR 與 AI 分類都會停止）。\n確定要結束嗎？", flags=MB_YESNO | MB_ICON_WARN | 0x100) == IDYES:
            log("使用者從選單結束")
            try:
                app.deregister()
            except Exception:
                pass
            icon.stop()
            stop.set()
            os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem(lambda i: "狀態：" + ("運作中" if (port_open(app.PORT) and port_open(app.BRIDGE_PORT)) else "有服務沒在跑"), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("開啟狀態視窗", show_status, default=True),
        pystray.MenuItem("複製 OCR 主機網址", copy_url("url")),
        pystray.MenuItem("複製 AI 橋接網址", copy_url("aiUrl")),
        pystray.MenuItem("設定顯示名稱…", set_name),
        pystray.MenuItem("開機自動啟動", toggle_autostart, checked=lambda i: autostart_get()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("開啟紀錄資料夾", open_logs),
        pystray.MenuItem("重新啟動", restart),
        pystray.MenuItem("結束（停止主機）", quit_app),
    )
    icon = pystray.Icon("ZiziOCR", make_icon(False), TITLE + "：啟動中…", menu)

    def watcher():
        last = None
        while not stop.is_set():
            ocr, ai, _ = status()
            ok = ocr and ai
            if ok != last:
                icon.icon = make_icon(ok)
                icon.title = TITLE + "：" + ("運作中" if ok else "有服務沒在跑")
                last = ok
            stop.wait(5)

    def first_run():
        c = hostlib.load_config()
        if not c.get("displayName") and not c.get("askedName"):
            n = input_box("這台主機要在網站上顯示什麼名稱？（例如：劍潭；留空就用電腦名稱）", TITLE, "")
            c["askedName"] = True
            if n:
                c["displayName"] = n.strip()
            hostlib.save_config(c)

    def setup(icon):
        icon.visible = True
        threading.Thread(target=watcher, daemon=True).start()
        threading.Thread(target=first_run, daemon=True).start()
        try:
            icon.notify("已在背景執行。點右下角圖示可查看狀態。", TITLE)
        except Exception:
            pass

    icon.run(setup)
