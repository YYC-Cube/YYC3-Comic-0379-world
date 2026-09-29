"""
G1-TC-G1-004 路径归一中间件逻辑单元测试
覆盖：normalize_nas_path 归一规则 + is_nas_path 判定（含 OBS-G1-004-1
前缀边界回归锚：/mnt/nasdir 等共享前缀目录不得误判为 NAS 路径）
被测文件无第三方依赖，直接 importlib 加载，无需桩模块
"""
import importlib.util
import sys
from pathlib import Path

# ── 导入被测中间件（相对本文件定位，避免依赖运行目录）──
_mw_path = Path(__file__).resolve().parent.parent / "app" / "api" / "middleware" / "path_normalize.py"
_spec = importlib.util.spec_from_file_location("yyc3_path_normalize", _mw_path)
if _spec is None or _spec.loader is None:
    raise SystemExit(f"无法加载被测模块: {_mw_path}")
pn_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pn_mod)

normalize_nas_path = pn_mod.normalize_nas_path
is_nas_path = pn_mod.is_nas_path
NAS_STANDARD_ROOT = pn_mod.NAS_STANDARD_ROOT

# ── 用例 ──
ok = 0
total = 0


def check(name, cond):
    global ok, total
    total += 1
    ok += int(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}")


# 一、normalize_nas_path 归一规则
check("空值归一为 NAS 根", normalize_nas_path(None) == "/mnt/nas")
check("空串归一为 NAS 根", normalize_nas_path("") == "/mnt/nas")
check("标准子路径原样保留", normalize_nas_path("/mnt/nas/projects/x") == "/mnt/nas/projects/x")
check("标准根本身保留", normalize_nas_path("/mnt/nas") == "/mnt/nas")
check("Mac 别名归一", normalize_nas_path("/Volumes/nas/projects/x") == "/mnt/nas/projects/x")
check("Mac 别名大写变体归一", normalize_nas_path("/Volumes/NAS/a") == "/mnt/nas/a")
check("相对路径拼接 NAS 根", normalize_nas_path("projects/x") == "/mnt/nas/projects/x")
check("多余斜杠压缩", normalize_nas_path("//mnt//nas///a//b") == "/mnt/nas/a/b")
check("非 NAS 绝对路径不篡改", normalize_nas_path("/home/user/data") == "/home/user/data")

# 二、is_nas_path 判定（正向）
check("is_nas 标准子路径 -> True", is_nas_path("/mnt/nas/projects/x") is True)
check("is_nas 根本身 -> True", is_nas_path("/mnt/nas") is True)
check("is_nas Mac 别名归一后 -> True", is_nas_path("/Volumes/nas/projects/x") is True)
check("is_nas 相对路径归一后 -> True", is_nas_path("projects/x") is True)

# 三、is_nas_path 判定（负向）
check("is_nas 空值 -> False", is_nas_path(None) is False)
check("is_nas 空串 -> False", is_nas_path("") is False)
check("is_nas 非 NAS 绝对路径 -> False", is_nas_path("/home/user/data") is False)
# OBS-G1-004-1 回归锚：共享前缀但非子路径的目录不得误判
check("is_nas /mnt/nasdir/x -> False（边界修复锚点）", is_nas_path("/mnt/nasdir/x") is False)
check("is_nas /mnt/nasx -> False（无斜杠边界）", is_nas_path("/mnt/nasx") is False)
check("is_nas /Volumes/nasdir/x -> False（别名前缀边界）", is_nas_path("/Volumes/nasdir/x") is False)

print(f"\nG1-004 路径归一逻辑单测：{ok}/{total} 通过")
sys.exit(0 if ok == total else 1)
