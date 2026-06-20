#!/usr/bin/env python3
"""
xgkb_sync_dir.py — 批量同步整个目录到玄关个人知识库。

用法:
  python3 xgkb_sync_dir.py <目录路径>
  python3 xgkb_sync_dir.py <目录路径> --interval 5

特性:
  - 扫描目录下所有 .md/.txt/.json/.yaml 文件
  - 逐个调用 xgkb_push 同步
  - 自动间隔避免限流（默认 3 秒）
  - 失败的自动重试一次
  - 输出同步报告
"""
import sys
import time
import subprocess
from pathlib import Path

# Allowed text file extensions
ALLOWED_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml"}

# Max file size (200KB → use push, larger → use upload_file)
PUSH_SIZE_THRESHOLD = 200 * 1024

# Default interval between uploads (seconds)
DEFAULT_INTERVAL = 3

# Script paths
SCRIPTS_DIR = Path(__file__).parent
PUSH_SCRIPT = SCRIPTS_DIR / "xgkb_push.py"
UPLOAD_FILE_SCRIPT = SCRIPTS_DIR / "xgkb_upload_file.py"


def sync_directory(dir_path: str, interval: float = DEFAULT_INTERVAL) -> int:
    """Sync all text files in a directory to XGKB."""
    directory = Path(dir_path).resolve()

    if not directory.exists():
        print(f"[xgkb-sync-dir] 目录不存在: {dir_path}", file=sys.stderr)
        return 1

    if not directory.is_dir():
        print(f"[xgkb-sync-dir] 不是目录: {dir_path}", file=sys.stderr)
        return 1

    # Scan files
    files = []
    for f in directory.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        # Skip hidden files and .xgkb.json
        if f.name.startswith(".") or f.name == ".xgkb.json":
            continue
        files.append(f)

    if not files:
        print(f"[xgkb-sync-dir] 未找到可同步的文件: {dir_path}")
        return 0

    print(f"[xgkb-sync-dir] 发现 {len(files)} 个文件，开始同步...")

    success = 0
    failed = 0
    retried_success = 0
    errors = []

    for i, f in enumerate(files):
        rel = f.relative_to(directory)

        # Choose script based on file size
        use_upload = f.stat().st_size >= PUSH_SIZE_THRESHOLD and UPLOAD_FILE_SCRIPT.exists()
        script = UPLOAD_FILE_SCRIPT if use_upload else PUSH_SCRIPT

        # First attempt
        proc = subprocess.run(
            ["python3", str(script), str(f)],
            capture_output=True,
            text=True,
            timeout=30
        )

        if proc.returncode == 0:
            success += 1
            print(f"  [{i+1}/{len(files)}] ✅ {rel}")
        else:
            # Retry once
            time.sleep(2)
            proc2 = subprocess.run(
                ["python3", str(script), str(f)],
                capture_output=True,
                text=True,
                timeout=30
            )
            if proc2.returncode == 0:
                retried_success += 1
                print(f"  [{i+1}/{len(files)}] ✅ {rel} (retry)")
            else:
                failed += 1
                err = proc2.stderr.strip()[:100] if proc2.stderr else "unknown"
                errors.append(str(rel))
                print(f"  [{i+1}/{len(files)}] ❌ {rel}: {err}", file=sys.stderr)

        # Interval between uploads (skip after last file)
        if i < len(files) - 1 and interval > 0:
            time.sleep(interval)

    # Summary
    print()
    print(f"[xgkb-sync-dir] 完成：✅ {success} 成功 + {retried_success} 重试成功 / ❌ {failed} 失败 / 共 {len(files)}")

    if errors:
        print("[xgkb-sync-dir] 失败文件：")
        for e in errors:
            print(f"  - {e}")

    return 0 if failed == 0 else 1


def main():
    if len(sys.argv) < 2:
        print("用法: python3 xgkb_sync_dir.py <目录路径> [--interval <秒数>]")
        return 1

    dir_path = sys.argv[1]
    interval = DEFAULT_INTERVAL

    # Parse optional --interval
    for i, arg in enumerate(sys.argv[2:], 2):
        if arg == "--interval" and i + 1 < len(sys.argv):
            try:
                interval = float(sys.argv[i + 1])
            except ValueError:
                pass

    return sync_directory(dir_path, interval)


if __name__ == "__main__":
    sys.exit(main())
