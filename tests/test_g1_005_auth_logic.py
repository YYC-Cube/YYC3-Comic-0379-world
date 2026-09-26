"""
G1-TC-G1-005 鉴权中间件逻辑单元测试
环境：jwt/fastapi/starlette 未安装 → 以桩模块注入，验证核心逻辑正确性
E2E（网关实际请求 401/401/200）需启动网关 + NAS，标记 BLOCKED
"""
import sys
import types

# ── 桩：jwt（round-trip 伪实现）──
_jwt_store = {}
_jwt_counter = [0]
jwt_stub = types.ModuleType("jwt")


def _encode(payload, key, algorithm=None, **kw):
    global _jwt_counter
    _jwt_counter[0] += 1
    tok = f"jwt.{_jwt_counter[0]}"
    _jwt_store[tok] = dict(payload)
    return tok


def _decode(token, key, algorithms=None, **kw):
    if token not in _jwt_store:
        raise Exception("invalid token")
    return _jwt_store[token]


jwt_stub.encode = _encode
jwt_stub.decode = _decode
jwt_stub.ExpiredSignatureError = type("ExpiredSignatureError", (Exception,), {})
jwt_stub.InvalidTokenError = type("InvalidTokenError", (Exception,), {})
sys.modules["jwt"] = jwt_stub

# ── 桩：fastapi ──
fastapi_stub = types.ModuleType("fastapi")


class _HTTPException(Exception):
    def __init__(self, status_code=401, detail=""):
        self.status_code = status_code
        self.detail = detail


class _Request:
    pass


fastapi_stub.HTTPException = _HTTPException
fastapi_stub.Request = _Request
fastapi_stub.status = types.SimpleNamespace(HTTP_401_UNAUTHORIZED=401, HTTP_403_FORBIDDEN=403)
fs_sec = types.ModuleType("fastapi.security")


class _HTTPBearer:
    def __init__(self, auto_error=False):
        pass


fs_sec.HTTPBearer = _HTTPBearer
sys.modules["fastapi"] = fastapi_stub
sys.modules["fastapi.security"] = fs_sec

# ── 桩：starlette ──
starlette_stub = types.ModuleType("starlette")
star_mw = types.ModuleType("starlette.middleware")
star_mw_base = types.ModuleType("starlette.middleware.base")


class _BaseHTTPMiddleware:
    def __init__(self, app=None, *a, **kw):
        self.app = app


star_mw_base.BaseHTTPMiddleware = _BaseHTTPMiddleware
sys.modules["starlette"] = starlette_stub
sys.modules["starlette.middleware"] = star_mw
sys.modules["starlette.middleware.base"] = star_mw_base
star_resp = types.ModuleType("starlette.responses")


class _JSONResponse:
    def __init__(self, content=None, status_code=200):
        self.content = content
        self.status_code = status_code


star_resp.JSONResponse = _JSONResponse
sys.modules["starlette.responses"] = star_resp

# ── 桩：app.config.settings ──
app_stub = types.ModuleType("app")
app_cfg = types.ModuleType("app.config")
app_cfg.settings = types.SimpleNamespace(
    auth_enabled=True,
    jwt_secret_key="test-secret-not-for-prod",
    jwt_algorithm="HS256",
    jwt_expiration_hours=24,
    api_keys="sk-test-valid-001,sk-test-valid-002",
    admin_api_keys="sk-test-admin-001",
)
sys.modules["app"] = app_stub
sys.modules["app.config"] = app_cfg

# ── 桩：app.cache（rate_limit 中间件连带导入，仅占位）──
app_cache = types.ModuleType("app.cache")
app_cache.redis_client = None
sys.modules["app.cache"] = app_cache

# ── 导入被测中间件（直接加载 auth.py，绕过 api/middleware/__init__ 连带导入）──
import importlib.util

_auth_path = "../core/api/middleware/auth.py"
_spec = importlib.util.spec_from_file_location("yyc3_auth", _auth_path)
auth_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(auth_mod)

auth_config = auth_mod.auth_config
hash_api_key = auth_mod.hash_api_key
verify_api_key = auth_mod.verify_api_key
generate_jwt_token = auth_mod.generate_jwt_token
verify_jwt_token = auth_mod.verify_jwt_token
AuthMiddleware = auth_mod.AuthMiddleware

mw = AuthMiddleware(app=lambda x: x)

# ── 用例 ──
ok = 0
total = 0


def check(name, cond):
    global ok, total
    total += 1
    ok += int(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}")


# 1. hash_api_key 确定性（SHA-256）
h1 = hash_api_key("sk-test-valid-001")
h2 = hash_api_key("sk-test-valid-001")
check("hash_api_key 确定性（同 key 同哈希）", h1 == h2 and len(h1) == 64)

# 2. verify_api_key 命中
check("verify_api_key 合法 key 返回 True", verify_api_key("sk-test-valid-001") is True)
# 3. verify_api_key 未命中
check("verify_api_key 非法 key 返回 False", verify_api_key("sk-evil-000") is False)

# 4. _should_skip_auth
check("skip /health", mw._should_skip_auth("/health") is True)
check("skip /v1/health", mw._should_skip_auth("/v1/health") is True)
check("skip /docs/x", mw._should_skip_auth("/docs/x") is True)
check("not skip /v1/chat/completions", mw._should_skip_auth("/v1/chat/completions") is False)

# 5. _is_admin_request
check("admin /v1/admin/keys", mw._is_admin_request("/v1/admin/keys") is True)
check("not admin /v1/admin/dashboard（看板壳特例）", mw._is_admin_request("/v1/admin/dashboard") is False)

# 6. JWT 生成 + 校验（桩 round-trip）
tok = generate_jwt_token("tester_001", expires_hours=1)
payload = verify_jwt_token(tok)
check("JWT 生成并校验成功", payload is not None and payload.get("user_id") == "tester_001")

print(f"\nG1-005 鉴权逻辑单测：{ok}/{total} 通过")
sys.exit(0 if ok == total else 1)
