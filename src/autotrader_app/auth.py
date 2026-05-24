"""授权码验证模块。

启动时联网请求服务器验证授权码，验证通过才能使用。
"""
from __future__ import annotations

import hashlib
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

# ── 授权服务器地址（部署你的验证服务后修改这个地址）──
AUTH_SERVER = "https://www.91aigu.com/api/auth"
# ── 本地授权缓存文件（验证通过后缓存，下次免网络验证）──
_AUTH_CACHE_FILE = Path(__file__).resolve().parent.parent.parent / ".license"


def generate_machine_code() -> str:
    """生成机器码（基于硬件信息，更换电脑后失效）。"""
    import uuid
    mac = uuid.UUID(int=uuid.getnode()).hex[-12:]
    return hashlib.md5(mac.encode()).hexdigest()[:16]


def verify_license(key: str) -> tuple[bool, str]:
    """联网验证授权码。

    Args:
        key: 用户输入的授权码。

    Returns:
        (True, "") 验证通过
        (False, "错误信息") 验证失败
    """
    if not key or len(key) < 8:
        return False, "授权码格式不正确"

    if requests is None:
        return False, "网络模块不可用，请安装 requests"

    machine_code = generate_machine_code()

    try:
        resp = requests.post(
            AUTH_SERVER,
            json={"key": key, "machine": machine_code},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                _save_license_cache(key, machine_code)
                return True, ""
            return False, data.get("message", "授权码无效")
        return False, "服务器连接失败"
    except requests.exceptions.Timeout:
        # 超时时尝试本地缓存
        return _check_local_cache(key, machine_code)
    except Exception as exc:
        return False, f"验证失败：{exc}"


def _save_license_cache(key: str, machine: str) -> None:
    """本地缓存授权信息（有效期 7 天）。"""
    try:
        expire = int(time.time()) + 7 * 86400
        content = f"{key}|{machine}|{expire}"
        _AUTH_CACHE_FILE.write_text(content, encoding="utf-8")
    except Exception:
        pass


def _check_local_cache(key: str, machine: str) -> tuple[bool, str]:
    """检查本地授权缓存（网络不可用时使用）。"""
    try:
        if not _AUTH_CACHE_FILE.exists():
            return False, "网络不可用，且无本地授权缓存"
        content = _AUTH_CACHE_FILE.read_text(encoding="utf-8").strip()
        parts = content.split("|")
        if len(parts) != 3:
            return False, "授权缓存已损坏"
        cached_key, cached_machine, expire_str = parts
        if cached_key != key or cached_machine != machine:
            return False, "授权缓存不匹配"
        if int(time.time()) > int(expire_str):
            _AUTH_CACHE_FILE.unlink(missing_ok=True)
            return False, "本地授权已过期，请联网重新验证"
        return True, ""
    except Exception:
        return False, "授权缓存读取失败"
