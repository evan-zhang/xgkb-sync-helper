#!/usr/bin/env python3
"""
xgkb_retry_failed.py — 从 xgkb-push.log 提取失败文件，重新 push。

用法：
  cd /root/.openclaw/skills/xgkb-sync-helper/scripts
  /root/.local/bin/python3.11 xgkb_retry_failed.py /tmp/xgkb-push.log
  /root/.local/bin/python3.11 xgkb_retry_failed.py /tmp/xgkb-push.log --dry-run
"""
import re
import subprocess
import sys
from pathlib import Path

PUSH_SCRIPT = Path("/root/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_push.py")
PYTHON = "/root/.local/bin/python3.11"
LOG = sys.argv[1] if len(sys.argv) > 1 else "/tmp/xgkb-push.log"
DRY = "--dry-run" in sys.argv

PROJ_ROOT = Path("/root/projects")
FAIL_RE = re.compile(r"^  ✗ .+?: (.+?): API error")

files = []
for line in Path(LOG).read_text(encoding="utf-8", errors="replace").splitlines():
    m = FAIL_RE.match(line)
    if m:
        rel = m.group(1).strip()
        files.append(rel)

print(f"[retry] 从 {LOG} 提取到 {len(files)} 个失败文件")
if not files:
    sys.exit(0)

if DRY:
    for f in files:
        print(f"  [DRY] would retry: {f}")
    sys.exit(0)

ok = 0
fail = 0
err_remain = []
for i, rel in enumerate(files, 1):
    local = PROJ_ROOT / rel
    if not local.exists():
        print(f"  ✗ 本地不存在: {local}")
        fail += 1
        continue
    print(f"[{i}/{len(files)}] retry: {rel}")
    r = subprocess.run(
        [PYTHON, str(PUSH_SCRIPT), str(local)],
        cwd=str(PUSH_SCRIPT.parent),
        capture_output=True, text=True, timeout=60,
    )
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0 and "失败" not in out and "error" not in out.lower():
        print(f"    ✓ {out.splitlines()[-1] if out else 'ok'}")
        ok += 1
    else:
        print(f"    ✗ {out.splitlines()[-1] if out else 'no output'}")
        err_remain.append(rel)
        fail += 1

print(f"\n[retry] ✓ {ok} ok, ✗ {fail} still failing")
if err_remain:
    print(f"[retry] 仍失败 {len(err_remain)} 个，再次跑本脚本可继续重试")
    Path("/tmp/xgkb-retry-remaining.txt").write_text("\n".join(err_remain), encoding="utf-8")