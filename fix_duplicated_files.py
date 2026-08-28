# -*- coding: utf-8 -*-
from __future__ import annotations  # 兼容 Python 3.8 的 | 类型注解

"""清除因“重复导入未覆盖”产生的 “(1)” 后缀文件，并用它们覆盖原文件。

场景：重新上传插件文件时忘记勾选“覆盖”，导致新文件变成
  api.py      -> 旧文件（仍在被插件使用）
  api (1).py  -> 新文件（含最新修改，但未被使用）

本脚本会把每个 “(1)” 文件的内容覆盖到对应的原文件上，然后删除 “(1)” 文件。

用法：
  python fix_duplicated_files.py                # 处理脚本所在目录（含子目录）
  python fix_duplicated_files.py --dir <路径>   # 处理指定目录
  python fix_duplicated_files.py --dry-run      # 仅预览，不实际修改
  python fix_duplicated_files.py --no-delete    # 覆盖后保留 “(1)” 文件（不删除）

说明：
  - 支持 “name (1).py”、“name(1).py”、“name（1）.py” 以及 “name.py (1)” 等常见形式
  - 只有对应的原文件存在时才覆盖；原文件不存在时仅提示，不删除
  - 建议在机器人停止运行时执行
"""

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

# Windows 控制台默认 GBK，强制 UTF-8 输出避免中文乱码
if os.name == "nt":
    try:
        os.system("chcp 65001 > nul")  # noqa: S605
    except Exception:
        pass
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 匹配 "xxx (1).py" / "xxx(1).py" / "xxx（1）.py"
_DUP_TAIL = re.compile(r"^(?P<base>.*?)\s*[（(]\s*\d+\s*[）)](?P<ext>\.[^.]+)$")
# 匹配 "xxx.py (1)" / "xxx.py(1)" / "xxx.py（1）"
_DUP_APPEND = re.compile(r"^(?P<base>.*?\.[^.]+)\s*[（(]\s*\d+\s*[）)]$")


def parse_duplicate(name: str) -> str | None:
    """把带 (1) 后缀的文件名还原为原文件名；不匹配返回 None。"""
    m = _DUP_TAIL.match(name)
    if m:
        return m.group("base") + m.group("ext")
    m = _DUP_APPEND.match(name)
    if m:
        return m.group("base")
    return None


def collect(base_dir: Path) -> list[tuple[Path, Path]]:
    """返回 [(dup_file, original_file), ...]"""
    pairs = []
    for dup in base_dir.rglob("*"):
        if not dup.is_file():
            continue
        original_name = parse_duplicate(dup.name)
        if original_name is None:
            continue
        original = dup.parent / original_name
        if not original.exists():
            print(f"[跳过] 原文件不存在，保留并跳过: {dup} -> {original}")
            continue
        pairs.append((dup, original))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description="用 (1) 后缀文件覆盖原文件")
    parser.add_argument("--dir", default=str(Path(__file__).resolve().parent),
                        help="要处理的目录（默认：脚本所在目录，递归处理）")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不实际修改")
    parser.add_argument("--no-delete", action="store_true", help="覆盖后保留 (1) 文件")
    args = parser.parse_args()

    base_dir = Path(args.dir).resolve()
    if not base_dir.is_dir():
        print(f"目录不存在: {base_dir}")
        return 1

    pairs = collect(base_dir)
    if not pairs:
        print("没有找到带 (1) 后缀的文件。")
        return 0

    print(f"共找到 {len(pairs)} 个带后缀文件（目录: {base_dir}）\n")
    replaced = 0
    for dup, original in pairs:
        action = "[预览]" if args.dry_run else "[覆盖]"
        try:
            rel = original.relative_to(base_dir)
        except ValueError:
            rel = original
        print(f"{action} {dup.name}\n{'':6}-> {rel}")
        if args.dry_run:
            continue
        try:
            shutil.copy2(dup, original)  # copy2 保留元信息
            replaced += 1
        except OSError as e:
            print(f"  [失败] 覆盖 {original} 出错: {e}")
            continue
        if not args.no_delete:
            try:
                dup.unlink()
            except OSError as e:
                print(f"  [警告] 覆盖成功但删除 {dup} 失败（可手动删除）: {e}")

    if args.dry_run:
        print(f"\n预览完成，共 {len(pairs)} 个文件待处理（未实际修改）。")
    else:
        print(f"\n完成：覆盖 {replaced}/{len(pairs)} 个文件。")
        if args.no_delete:
            print("已按 --no-delete 保留 (1) 文件。")
        print("建议重启机器人并验证功能后再确认无误。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
