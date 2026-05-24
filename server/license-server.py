"""授权码验证服务 + 管理后台。

部署：
    pip install flask
    pm2 start auto-trade-license-server.py --interpreter python3 --name license-server
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request

app = Flask(__name__)

ADMIN_PASSWORD = "admin123456"  # ⚠️ 改成你自己的密码
DB_FILE = Path(__file__).parent / "licenses.json"


def load() -> dict:
    if DB_FILE.exists():
        return json.loads(DB_FILE.read_text("utf-8"))
    return {}

def save(data: dict) -> None:
    DB_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")

def new_key() -> str:
    return "AT-" + uuid.uuid4().hex[:8].upper()

def parse_duration(text: str) -> int:
    """解析时长文本为秒数。支持: 1h, 2d, 3w, 4m, 永久"""
    text = text.strip().lower()
    if text == "永久":
        return 0
    if text.endswith("h"):
        return int(text[:-1]) * 3600
    if text.endswith("d"):
        return int(text[:-1]) * 86400
    if text.endswith("w"):
        return int(text[:-1]) * 86400 * 7
    if text.endswith("m"):
        return int(text[:-1]) * 86400 * 30
    return int(text) * 86400  # 默认天


# ────────────────────────────────────────────────────────────
# API: 验证授权码
# ────────────────────────────────────────────────────────────

@app.route("/api/auth", methods=["POST"])
def verify():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"success": False, "message": "请求格式错误"}), 400

    key = data.get("key", "").strip().upper()
    machine = data.get("machine", "").strip()
    if not key:
        return jsonify({"success": False, "message": "授权码不能为空"}), 400
    if not machine:
        return jsonify({"success": False, "message": "机器码不能为空"}), 400

    db = load()
    info = db.get(key)
    if not info:
        return jsonify({"success": False, "message": "授权码不存在"}), 200

    expire = info.get("expire_at", 0)
    if expire > 0 and time.time() > expire:
        return jsonify({"success": False, "message": "授权码已过期"}), 200

    bound = info.get("bound_machine", "")
    if bound and bound != machine:
        return jsonify({"success": False, "message": "已绑定其他电脑"}), 200

    if not bound:
        info["bound_machine"] = machine
        info["activated_at"] = time.time()
        save(db)

    remain = "永久" if expire == 0 else f"{(expire - time.time()) / 86400:.0f}天"
    return jsonify({"success": True, "message": "验证通过", "remain": remain})


# ────────────────────────────────────────────────────────────
# API: 生成授权码（管理员）
# ────────────────────────────────────────────────────────────

@app.route("/api/auth/generate", methods=["POST"])
def generate():
    data = request.get_json(force=True, silent=True)
    if not data or data.get("admin_key") != ADMIN_PASSWORD:
        return jsonify({"success": False, "message": "管理员密码错误"}), 200

    duration_text = data.get("duration", "30d")
    seconds = parse_duration(duration_text)
    remark = data.get("remark", "")

    key = new_key()
    db = load()
    db[key] = {
        "created_at": time.time(),
        "expire_at": time.time() + seconds if seconds > 0 else 0,
        "duration_text": duration_text,
        "bound_machine": "",
        "activated_at": 0,
        "remark": remark,
    }
    save(db)

    expire_str = "永久" if seconds == 0 else time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() + seconds))
    return jsonify({"success": True, "key": key, "expire": expire_str})


# ────────────────────────────────────────────────────────────
# API: 列出授权码（管理员）
# ────────────────────────────────────────────────────────────

@app.route("/api/auth/list", methods=["POST"])
def list_keys():
    data = request.get_json(force=True, silent=True)
    if not data or data.get("admin_key") != ADMIN_PASSWORD:
        return jsonify({"success": False, "message": "管理员密码错误"}), 200

    db = load()
    result = []
    for key, info in db.items():
        expire = info.get("expire_at", 0)
        remain = "永久" if expire == 0 else f"{(expire - time.time()) / 86400:.0f}天"
        result.append({
            "key": key,
            "duration": info.get("duration_text", ""),
            "expire": time.strftime("%Y-%m-%d %H:%M", time.localtime(expire)) if expire else "永久",
            "remain": remain,
            "bound": bool(info.get("bound_machine")),
            "machine": info.get("bound_machine", "")[:12] if info.get("bound_machine") else "",
            "remark": info.get("remark", ""),
            "created": time.strftime("%m-%d %H:%M", time.localtime(info.get("created_at", 0))),
        })
    return jsonify({"success": True, "licenses": result})


# ────────────────────────────────────────────────────────────
# API: 删除授权码（管理员）
# ────────────────────────────────────────────────────────────

@app.route("/api/auth/delete", methods=["POST"])
def delete_key():
    data = request.get_json(force=True, silent=True)
    if not data or data.get("admin_key") != ADMIN_PASSWORD:
        return jsonify({"success": False, "message": "管理员密码错误"}), 200
    key = data.get("key", "").strip().upper()
    if not key:
        return jsonify({"success": False, "message": "key 不能为空"}), 400
    db = load()
    if key in db:
        del db[key]
        save(db)
    return jsonify({"success": True, "message": "已删除"})


# ────────────────────────────────────────────────────────────
# 管理后台网页
# ────────────────────────────────────────────────────────────

@app.route("/api/auth/admin", methods=["GET"])
def admin_page():
    pwd = request.args.get("pwd", "")
    if pwd != ADMIN_PASSWORD:
        return """<html><body style="background:#111;color:#fff;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh">
        <form method="get" style="text-align:center">
        <h2>授权管理</h2>
        <input name="pwd" type="password" placeholder="管理员密码" style="padding:8px;width:200px">
        <button type="submit" style="padding:8px 20px">登录</button>
        </form></body></html>"""

    return """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>授权管理</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#0f172a; color:#e2e8f0; font-family:system-ui,sans-serif; padding:20px; max-width:900px; margin:auto; }
h1 { color:#f8fafc; margin-bottom:8px; }
.sub { color:#94a3b8; font-size:13px; margin-bottom:20px; }
.card { background:#1e293b; border:1px solid #334155; border-radius:8px; padding:16px; margin-bottom:16px; }
.row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin:8px 0; }
.row label { color:#94a3b8; font-size:13px; min-width:50px; }
.row input, .row select { background:#0f172a; border:1px solid #475569; border-radius:4px; color:#e2e8f0; padding:6px 10px; font-size:13px; }
.row input[type=text] { width:200px; }
.row input[type=password] { width:200px; }
.btn { background:#3b82f6; border:none; border-radius:4px; color:white; padding:6px 16px; cursor:pointer; font-size:13px; }
.btn:hover { background:#2563eb; }
.btn-success { background:#22c55e; }
.btn-success:hover { background:#16a34a; }
.btn-danger { background:#ef4444; }
.btn-danger:hover { background:#dc2626; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th, td { text-align:left; padding:8px 6px; border-bottom:1px solid #334155; }
th { color:#94a3b8; font-weight:500; font-size:12px; text-transform:uppercase; }
.badge { display:inline-block; padding:2px 6px; border-radius:3px; font-size:11px; }
.badge-green { background:#166534; color:#86efac; }
.badge-red { background:#7f1d1d; color:#fca5a5; }
#result { margin-top:8px; font-size:13px; }
</style></head>
<body>
<h1>🔑 授权码管理</h1>
<p class="sub">生成 / 管理软件授权码</p>

<div class="card">
<h3 style="margin-bottom:8px;">生成授权码</h3>
<div class="row"><label>时长</label>
<select id="duration">
<option value="1h">1 小时</option>
<option value="6h">6 小时</option>
<option value="12h">12 小时</option>
<option value="1d">1 天</option>
<option value="3d">3 天</option>
<option value="7d">7 天</option>
<option value="14d">14 天</option>
<option value="30d" selected>30 天</option>
<option value="60d">60 天</option>
<option value="90d">90 天</option>
<option value="180d">180 天</option>
<option value="365d">365 天</option>
<option value="永久">永久</option>
</select></div>
<div class="row"><label>备注</label><input type="text" id="remark" placeholder="买家名称/用途"></div>
<div class="row"><button class="btn btn-success" onclick="generate()">生成授权码</button></div>
<div id="result"></div>
</div>

<div class="card">
<h3 style="margin-bottom:8px;">授权码列表 <span id="count" style="color:#94a3b8;font-size:13px;"></span></h3>
<table><thead><tr><th>授权码</th><th>时长</th><th>到期</th><th>剩余</th><th>绑定</th><th>备注</th><th>操作</th></tr></thead>
<tbody id="list"></tbody></table>
</div>

<p class="sub">管理员密码请编辑脚本中的 ADMIN_PASSWORD 变量</p>

<script>
const PWD = new URLSearchParams(location.search).get('pwd');

async function api(path, body) {
    const r = await fetch(path, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...body, admin_key:PWD}) });
    return r.json();
}

async function generate() {
    const duration = document.getElementById('duration').value;
    const remark = document.getElementById('remark').value;
    const r = await api('/api/auth/generate', { duration, remark });
    document.getElementById('result').innerHTML = r.success
        ? `<span style="color:#22c55e">✅ ${r.key} (${r.expire})</span>`
        : `<span style="color:#ef4444">❌ ${r.message}</span>`;
    if (r.success) loadList();
}

async function delKey(key) {
    if (!confirm('删除 ' + key + '？')) return;
    await api('/api/auth/delete', { key });
    loadList();
}

async function loadList() {
    const r = await api('/api/auth/list', {});
    const tbody = document.getElementById('list');
    document.getElementById('count').textContent = r.success ? `(${r.licenses.length})` : '';
    if (!r.success) { tbody.innerHTML = '<tr><td colspan="7">加载失败</td></tr>'; return; }
    tbody.innerHTML = r.licenses.map(l => `<tr>
        <td><code style="color:#f59e0b">${l.key}</code></td>
        <td>${l.duration}</td>
        <td style="font-size:12px">${l.expire}</td>
        <td>${l.remain}</td>
        <td>${l.bound ? '<span class="badge badge-green">已绑定</span>' : '<span class="badge badge-red">未绑定</span>'}</td>
        <td style="color:#94a3b8;font-size:12px">${l.remark||''}</td>
        <td><button class="btn btn-danger" onclick="delKey('${l.key}')">删除</button></td>
    </tr>`).join('');
}
loadList();
</script>
</body></html>"""


if __name__ == "__main__":
    print("=" * 50)
    print("  授权服务器启动")
    print(f"  管理后台: http://0.0.0.0:8899/api/auth/admin?pwd=管理员密码")
    print(f"  验证接口: POST /api/auth")
    print("=" * 50)
    app.run(host="0.0.0.0", port=8899, debug=False)
