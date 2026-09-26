# -*- coding: utf-8 -*-
"""
BotHost — Backend điều khiển treo bot thật.
Chạy cùng web dashboard (index.html) trên cùng một dịch vụ.
Deploy: Render free + UptimeRobot ping giữ thức.
"""
# === TỰ CÀI ===
import subprocess
try:
    import flask
except:

    subprocess.run(["pip","install","-r","requirements.txt"])
# === HẾT ===

import os
import sys
import json
import time
import uuid
import hmac
import hashlib
import base64
import signal
import shutil
import zipfile
import subprocess
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
import psutil

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB / file

# ==================== CẤU HÌNH ====================
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "files")
LOG_DIR    = os.path.join(DATA_DIR, "logs")
DB_FILE    = os.path.join(DATA_DIR, "db.json")
for d in (DATA_DIR, UPLOAD_DIR, LOG_DIR):
    os.makedirs(d, exist_ok=True)

SECRET       = os.environ.get("SECRET_KEY", "change-me-in-production-please")
ADMIN_USER   = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASS   = os.environ.get("ADMIN_PASSWORD", "admin123")
MAX_FILES    = int(os.environ.get("MAX_FILES", "20"))
MAX_RUNNING  = int(os.environ.get("MAX_RUNNING", "5"))
ALLOWED_EXT  = {".py", ".js", ".zip"}

# ==================== DB ====================
def hash_pw(p):
    return hashlib.sha256(p.encode("utf-8")).hexdigest()

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                db = json.load(f)
            if "users" in db and "files" in db:
                return db
        except Exception:
            pass
    db = {
        "users": [{
            "id": "u_admin", "username": ADMIN_USER,
            "password": hash_pw(ADMIN_PASS), "role": "admin",
            "created_at": int(time.time() * 1000),
        }],
        "files": [],
    }
    save_db(db)
    return db

def save_db(db):
    tmp = DB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DB_FILE)

# ==================== TOKEN (30 ngày) ====================
def make_token(user):
    payload = json.dumps({
        "uid": user["id"], "u": user["username"],
        "exp": int(time.time()) + 30 * 86400,
    }).encode()
    b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = hmac.new(SECRET.encode(), b64.encode(), hashlib.sha256).hexdigest()[:32]
    return b64 + "." + sig

def verify_token(t):
    try:
        b64, sig = t.split(".")
        expected = hmac.new(SECRET.encode(), b64.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return None
        pad = "=" * (-len(b64) % 4)
        data = json.loads(base64.urlsafe_b64decode(b64 + pad).decode())
        if data["exp"] < time.time():
            return None
        return data
    except Exception:
        return None

def current_user():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    data = verify_token(auth[7:])
    if not data:
        return None
    db = load_db()
    return next((u for u in db["users"] if u["id"] == data["uid"]), None)

# ==================== TIẾN TRÌNH ====================
def pid_alive(pid):
    try:
        return psutil.pid_exists(int(pid))
    except Exception:
        return False

def sync_status(db):
    """Đánh dấu tiến trình đã chết (do restart server / crash) là stopped."""
    changed = False
    for f in db["files"]:
        if f.get("status") == "running" and f.get("pid"):
            if not pid_alive(f["pid"]):
                f["status"] = "stopped"
                f["pid"] = None
                f["started_at"] = None
                changed = True
    if changed:
        save_db(db)

def kill_pid(pid, force=False):
    if not pid or not pid_alive(pid):
        return
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        if hasattr(os, "killpg"):
            os.killpg(int(pid), sig)
        else:
            os.kill(int(pid), sig)
    except Exception:
        pass

def detect_entry(extracted_dir):
    """Tìm file chạy trong zip. Trả về (cmd_list, cwd) hoặc None."""
    for name in ("main.py", "app.py", "bot.py", "run.py", "start.py"):
        if os.path.isfile(os.path.join(extracted_dir, name)):
            return [sys.executable, name], extracted_dir
    if shutil.which("node"):
        for name in ("index.js", "app.js", "bot.js", "main.js"):
            if os.path.isfile(os.path.join(extracted_dir, name)):
                return ["node", name], extracted_dir
        if os.path.isfile(os.path.join(extracted_dir, "package.json")):
            return ["node", "."], extracted_dir
    return None

def start_process(db, rec):
    """Khởi chạy tiến trình thật. Trả về (ok, message)."""
    running = sum(1 for f in db["files"]
                  if f["user_id"] == rec["user_id"] and f.get("status") == "running")
    if running >= MAX_RUNNING:
        return False, "Đã đạt giới hạn %d tiến trình chạy đồng thời." % MAX_RUNNING

    ext = "." + rec["type"]
    cwd = os.path.dirname(rec["path"])

    if ext == ".zip":
        exdir = os.path.join(cwd, "extracted")
        if not os.path.isdir(exdir):
            os.makedirs(exdir, exist_ok=True)
            try:
                with zipfile.ZipFile(rec["path"]) as z:
                    z.extractall(exdir)
            except Exception as e:
                return False, "Giải nén thất bại: %s" % e
        entry = detect_entry(exdir)
        if not entry:
            return False, "Không tìm thấy file chạy trong zip (cần main.py/app.py/bot.py/index.js)."
        cmd, cwd = entry
    elif ext == ".py":
        cmd = [sys.executable, os.path.basename(rec["path"])]
    elif ext == ".js":
        if not shutil.which("node"):
            return False, "Server chưa cài Node.js — không chạy được file .js."
        cmd = ["node", os.path.basename(rec["path"])]
    else:
        return False, "Loại file không hỗ trợ."

    logf = open(rec["log_path"], "a", encoding="utf-8", buffering=1)
    logf.write("\n===== [%s] START %s =====\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), rec["name"]))
    logf.flush()

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=logf, stderr=subprocess.STDOUT,
            env=env, start_new_session=True,
        )
    except Exception as e:
        return False, "Lỗi khởi chạy: %s" % e

    rec["pid"] = proc.pid
    rec["status"] = "running"
    rec["started_at"] = int(time.time() * 1000)
    return True, "Đã chạy %s (PID %d)." % (rec["name"], proc.pid)

def user_files(db, uid):
    return [f for f in db["files"] if f["user_id"] == uid]

# ==================== ROUTES ====================
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/api/health")
def health():
    return jsonify({"ok": True, "mode": "real", "node": bool(shutil.which("node"))})

@app.route("/api/auth/login", methods=["POST"])
def login():
    d = request.get_json(force=True, silent=True) or {}
    u = (d.get("username") or "").strip()
    p = d.get("password") or ""
    db = load_db()
    user = next((x for x in db["users"] if x["username"].lower() == u.lower()), None)
    if not user or user["password"] != hash_pw(p):
        return jsonify({"error": "Sai tên đăng nhập hoặc mật khẩu."}), 401
    return jsonify({
        "token": make_token(user),
        "user": {"id": user["id"], "username": user["username"], "role": user["role"]},
    })

@app.route("/api/auth/register", methods=["POST"])
def register():
    d = request.get_json(force=True, silent=True) or {}
    u = (d.get("username") or "").strip()
    p = d.get("password") or ""
    if len(u) < 3:
        return jsonify({"error": "Username phải có ít nhất 3 ký tự."}), 400
    if len(p) < 6:
        return jsonify({"error": "Mật khẩu phải có ít nhất 6 ký tự."}), 400
    db = load_db()
    if any(x["username"].lower() == u.lower() for x in db["users"]):
        return jsonify({"error": "Username đã tồn tại."}), 400
    user = {
        "id": "u_" + uuid.uuid4().hex[:10], "username": u,
        "password": hash_pw(p), "role": "user",
        "created_at": int(time.time() * 1000),
    }
    db["users"].append(user)
    save_db(db)
    return jsonify({
        "token": make_token(user),
        "user": {"id": user["id"], "username": user["username"], "role": user["role"]},
    })

@app.route("/api/files")
def list_files():
    u = current_user()
    if not u:
        return jsonify({"error": "Unauthorized"}), 401
    db = load_db()
    sync_status(db)
    files = user_files(db, u["id"])
    q = (request.args.get("q") or "").lower()
    if q:
        files = [f for f in files if q in f["name"].lower()]
    now = int(time.time() * 1000)
    out = []
    for f in files:
        out.append({
            "id": f["id"], "name": f["name"], "type": f["type"], "size": f["size"],
            "status": f.get("status", "stopped"), "pid": f.get("pid"),
            "started_at": f.get("started_at"), "uploaded_at": f.get("uploaded_at"),
            "uptime_ms": (now - f["started_at"]) if f.get("status") == "running" and f.get("started_at") else 0,
        })
    running = sum(1 for f in files if f.get("status") == "running")
    storage = sum(f.get("size", 0) for f in files)
    return jsonify({
        "files": out,
        "stats": {
            "total": len(files), "running": running,
            "stopped": len(files) - running, "storage": storage,
            "limit_files": MAX_FILES, "limit_running": MAX_RUNNING,
        },
    })

@app.route("/api/files/upload", methods=["POST"])
def upload():
    u = current_user()
    if not u:
        return jsonify({"error": "Unauthorized"}), 401
    db = load_db()
    if len(user_files(db, u["id"])) >= MAX_FILES:
        return jsonify({"error": "Đã đạt giới hạn %d file/user." % MAX_FILES}), 400
    if "file" not in request.files:
        return jsonify({"error": "Không có file nào được gửi."}), 400
    f = request.files["file"]
    name = secure_filename(f.filename or "file")
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXT:
        return jsonify({"error": "Chỉ nhận file .py, .js hoặc .zip."}), 400

    fid = "f_" + uuid.uuid4().hex[:10]
    fdir = os.path.join(UPLOAD_DIR, fid)
    os.makedirs(fdir, exist_ok=True)
    fpath = os.path.join(fdir, name)
    f.save(fpath)
    size = os.path.getsize(fpath)
    if size > 50 * 1024 * 1024:
        shutil.rmtree(fdir, ignore_errors=True)
        return jsonify({"error": "File vượt quá 50MB."}), 400

    rec = {
        "id": fid, "user_id": u["id"], "name": name, "type": ext.lstrip("."),
        "size": size, "path": fpath, "log_path": os.path.join(LOG_DIR, fid + ".log"),
        "status": "stopped", "pid": None, "started_at": None,
        "uploaded_at": int(time.time() * 1000),
    }
    db["files"].append(rec)
    save_db(db)
    return jsonify({"ok": True, "file": {
        "id": rec["id"], "name": rec["name"], "type": rec["type"],
        "size": rec["size"], "status": "stopped", "uploaded_at": rec["uploaded_at"],
    }})

@app.route("/api/files/<fid>/start", methods=["POST"])
def start_file(fid):
    u = current_user()
    if not u:
        return jsonify({"error": "Unauthorized"}), 401
    db = load_db()
    sync_status(db)
    rec = next((f for f in db["files"] if f["id"] == fid and f["user_id"] == u["id"]), None)
    if not rec:
        return jsonify({"error": "Không tìm thấy file."}), 404
    if rec.get("status") == "running":
        return jsonify({"error": "File đang chạy."}), 400
    ok, msg = start_process(db, rec)
    if not ok:
        save_db(db)
        return jsonify({"error": msg}), 400
    save_db(db)
    return jsonify({"ok": True, "message": msg, "pid": rec["pid"]})

@app.route("/api/files/<fid>/stop", methods=["POST"])
def stop_file(fid):
    u = current_user()
    if not u:
        return jsonify({"error": "Unauthorized"}), 401
    db = load_db()
    sync_status(db)
    rec = next((f for f in db["files"] if f["id"] == fid and f["user_id"] == u["id"]), None)
    if not rec:
        return jsonify({"error": "Không tìm thấy file."}), 404
    pid = rec.get("pid")
    if pid and pid_alive(pid):
        kill_pid(pid, force=False)
        time.sleep(1.5)
        if pid_alive(pid):
            kill_pid(pid, force=True)
    try:
        with open(rec["log_path"], "a", encoding="utf-8") as lf:
            lf.write("===== [%s] STOPPED by user =====\n" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass
    rec["status"] = "stopped"
    rec["pid"] = None
    rec["started_at"] = None
    save_db(db)
    return jsonify({"ok": True, "message": "Đã dừng %s." % rec["name"]})

@app.route("/api/files/<fid>/restart", methods=["POST"])
def restart_file(fid):
    u = current_user()
    if not u:
        return jsonify({"error": "Unauthorized"}), 401
    db = load_db()
    sync_status(db)
    rec = next((f for f in db["files"] if f["id"] == fid and f["user_id"] == u["id"]), None)
    if not rec:
        return jsonify({"error": "Không tìm thấy file."}), 404
    pid = rec.get("pid")
    if pid and pid_alive(pid):
        kill_pid(pid, force=False)
        time.sleep(1.5)
        if pid_alive(pid):
            kill_pid(pid, force=True)
    rec["status"] = "stopped"
    rec["pid"] = None
    ok, msg = start_process(db, rec)
    if not ok:
        rec["status"] = "stopped"
        rec["pid"] = None
        save_db(db)
        return jsonify({"error": msg}), 400
    save_db(db)
    return jsonify({"ok": True, "message": msg, "pid": rec["pid"]})

@app.route("/api/files/<fid>", methods=["DELETE"])
def delete_file(fid):
    u = current_user()
    if not u:
        return jsonify({"error": "Unauthorized"}), 401
    db = load_db()
    rec = next((f for f in db["files"] if f["id"] == fid and f["user_id"] == u["id"]), None)
    if not rec:
        return jsonify({"error": "Không tìm thấy file."}), 404
    pid = rec.get("pid")
    if pid and pid_alive(pid):
        kill_pid(pid, force=True)
    shutil.rmtree(os.path.dirname(rec["path"]), ignore_errors=True)
    if os.path.exists(rec["log_path"]):
        try:
            os.remove(rec["log_path"])
        except Exception:
            pass
    db["files"] = [f for f in db["files"] if f["id"] != fid]
    save_db(db)
    return jsonify({"ok": True})

@app.route("/api/files/<fid>/log")
def get_log(fid):
    u = current_user()
    if not u:
        return jsonify({"error": "Unauthorized"}), 401
    db = load_db()
    sync_status(db)
    rec = next((f for f in db["files"] if f["id"] == fid and f["user_id"] == u["id"]), None)
    if not rec:
        return jsonify({"error": "Không tìm thấy file."}), 404
    lines = []
    if os.path.exists(rec["log_path"]):
        try:
            with open(rec["log_path"], "r", encoding="utf-8", errors="replace") as lf:
                lines = lf.readlines()[-200:]
        except Exception:
            lines = ["(lỗi đọc file log)\n"]
    if not lines:
        lines = ["(chưa có log — bấm Chạy để bắt đầu)\n"]
    now = int(time.time() * 1000)
    return jsonify({
        "lines": [l.rstrip("\n") for l in lines],
        "status": rec.get("status", "stopped"),
        "pid": rec.get("pid"),
        "uptime_ms": (now - rec["started_at"]) if rec.get("status") == "running" and rec.get("started_at") else 0,
    })

if __name__ == "__main__":
    load_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
