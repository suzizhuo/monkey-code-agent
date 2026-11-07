#!/usr/bin/env python3
"""五列记账工具：append / terminate 两个子命令。

用法:
  python3 scripts/ledger.py append <ledger.md> <谁执行了什么> <提出了什么> <修改了什么> <结果>
  python3 scripts/ledger.py terminate <ledger.md> [备注]

说明: 行号与时间戳自动生成；表格不存在则自动建表头。
"""
import sys
import datetime
import pathlib

HEADER = "| # | 谁执行了什么 | 提出了什么 | 修改了什么 | 结果 | 时间 |\n|---|---|---|---|---|---|\n"


def esc(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def rows(p: pathlib.Path):
    if not p.exists():
        return []
    return [
        l for l in p.read_text(encoding="utf-8").splitlines()
        if l.startswith("| ") and not l.startswith("| #") and not l.startswith("|---")
    ]


def append(p: pathlib.Path, cells):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    n = len(rows(p)) + 1
    if not p.exists():
        p.write_text("# 模糊扩展五列记账\n\n" + HEADER, encoding="utf-8")
    with p.open("a", encoding="utf-8") as f:
        f.write("| %d | %s | %s |\n" % (n, " | ".join(esc(c) for c in cells), ts))


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, path = sys.argv[1], pathlib.Path(sys.argv[2])
    if cmd == "append" and len(sys.argv) == 7:
        append(path, sys.argv[3:7])
    elif cmd == "terminate":
        note = sys.argv[3] if len(sys.argv) > 3 else "合并轮"
        append(path, [note, "无新增条目", "—", "终止（扩不出）"])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
