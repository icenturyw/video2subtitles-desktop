#!/usr/bin/env python3
"""Run lightweight project checks without downloading models or starting services."""
from __future__ import annotations

import py_compile
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {
    ".git",
    ".cache",
    ".venv",
    "venv",
    "__pycache__",
    "models",
    "output",
    "cache",
    "temp",
    "raw",
}


def _should_skip(path: Path) -> bool:
    return any(part in IGNORED_PARTS for part in path.relative_to(ROOT).parts)


def compile_sources() -> bool:
    files = sorted(path for path in ROOT.rglob("*.py") if not _should_skip(path))
    print(f"正在检查 Python 语法，共 {len(files)} 个文件...")
    ok = True
    for path in files:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            ok = False
            print(f"[FAIL] 语法检查失败: {path.relative_to(ROOT)}")
            print(exc)
    if ok:
        print("[OK] Python 语法检查通过")
    return ok


def run_tests() -> bool:
    tests_dir = ROOT / "tests"
    if not tests_dir.exists():
        print("[INFO] 未找到 tests/ 目录，跳过单元测试")
        return True
    print("正在运行基础单元测试...")
    suite = unittest.defaultTestLoader.discover(str(tests_dir))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return result.wasSuccessful()


def main() -> int:
    sys.path.insert(0, str(ROOT))
    compile_ok = compile_sources()
    tests_ok = run_tests()
    if compile_ok and tests_ok:
        print("[OK] 项目轻量检查通过")
        return 0
    print("[FAIL] 项目轻量检查失败")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
