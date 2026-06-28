#!/usr/bin/env python3
"""
xgkb_sync_dir.py — 批量同步整个目录到玄关知识库

用法:
  python3 xgkb_sync_dir.py <目录路径>
  python3 xgkb_sync_dir.py <目录路径> --interval 5
  python3 xgkb_sync_dir.py <目录路径> --dry-run
  python3 xgkb_sync_dir.py <目录路径> --pattern "*.md,*.txt"
"""

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from xgkb_push import push_file, find_project_config, load_agent_config, DEFAULT_REMOTE_ROOT

SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".html", ".htm", ".csv", ".xml"}


def scan_directory(dir_path: str, pattern_str: str | None = None) -> list[Path]:
    """递归扫描目录下所有支持的文本文件"""
    root = Path(dir_path).resolve()
    if not root.is_dir():
        print(f"[xgkb-sync-dir] ❌ 不是有效目录: {dir_path}", file=sys.stderr)
        return []

    if pattern_str:
        # 用户自定义 pattern
        patterns = [p.strip() for p in pattern_str.split(",") if p.strip()]
        import fnmatch
        files = []
        for dirpath, _, filenames in os.walk(root):
            for fname in filenames:
                rel = os.path.relpath(os.path.join(dirpath, fname), root)
                if any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(fname, p) for p in patterns):
                    files.append(Path(dirpath) / fname)
        return sorted(files)
    else:
        # 默认：按扩展名过滤
        files = []
        for dirpath, _, filenames in os.walk(root):
            for fname in filenames:
                ext = Path(fname).suffix.lower()
                if ext in SUPPORTED_EXTENSIONS:
                    files.append(Path(dirpath) / fname)
        return sorted(files)


def main():
    parser = argparse.ArgumentParser(description="批量同步目录到玄关知识库")
    parser.add_argument("directory", help="要同步的目录路径")
    parser.add_argument("--interval", type=float, default=3.0, help="文件间间隔秒数（默认 3）")
    parser.add_argument("--dry-run", action="store_true", help="预览模式：只列出文件，不推送")
    parser.add_argument("--pattern", type=str, default=None, help="自定义文件匹配（逗号分隔，如 *.md,*.txt）")
    args = parser.parse_args()

    dir_path = args.directory
    if not os.path.isdir(dir_path):
        print(f"[xgkb-sync-dir] ❌ 目录不存在: {dir_path}", file=sys.stderr)
        sys.exit(1)

    # 检查配置（从目录位置定位 workspace）
    global_cfg = load_agent_config(dir_path)
    if not global_cfg.get("appKey"):
        print("[xgkb-sync-dir] ❌ 未配置 appKey（Agent workspace .xgkb.json），无法同步", file=sys.stderr)
        sys.exit(1)

    # 扫描文件
    print(f"[xgkb-sync-dir] 扫描 {dir_path} ...")
    files = scan_directory(dir_path, args.pattern)
    print(f"[xgkb-sync-dir] 发现 {len(files)} 个文件")

    if not files:
        print("[xgkb-sync-dir] 没有需要同步的文件")
        return

    if args.dry_run:
        print("[xgkb-sync-dir] 🔍 预览模式（不实际推送）:")
        for f in files:
            print(f"  {f}")
        print(f"[xgkb-sync-dir] 共 {len(files)} 个文件待同步")
        return

    # 同步
    success = 0
    retry_success = 0
    failed = []
    total = len(files)

    for i, filepath in enumerate(files):
        print(f"\n[{i+1}/{total}] {filepath}")

        # 第一次尝试
        try:
            push_file(str(filepath))
            success += 1
        except SystemExit:
            pass
        except Exception as e:
            # 第一次失败，重试一次
            print(f"  ⚠️ 第一次失败: {e}，等待 5 秒后重试...")
            time.sleep(5)
            try:
                push_file(str(filepath))
                retry_success += 1
            except SystemExit:
                pass
            except Exception as e2:
                print(f"  ❌ 重试也失败: {e2}")
                failed.append((str(filepath), str(e2)))

        # 间隔（最后一个不等）
        if i < total - 1 and args.interval > 0:
            time.sleep(args.interval)

    # 报告
    print(f"\n{'='*50}")
    print(f"[xgkb-sync-dir] 同步完成: 成功={success} 重试成功={retry_success} 失败={len(failed)}")
    if failed:
        print("[xgkb-sync-dir] ❌ 失败文件:")
        for fpath, err in failed:
            print(f"  - {fpath} ({err})")

    # 有失败则返回非零退出码
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
