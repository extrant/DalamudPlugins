"""把 pluginmaster.json 里各插件的 DownloadCount 换成真实下载量（GitHub Actions 定时跑，也可以本地跑）。

- 只处理下载地址是 GitHub Release 资源的插件（https://github.com/<owner>/<repo>/releases/latest/download/<文件> 或
  .../releases/download/<标签>/<文件>）：把这个仓库所有 Release 里同名文件的 download_count 加起来
  （Dalamud 每次安装、更新都会下载一次，所以是安装 + 更新的总次数）；LastUpdate 换成最新一个带这个文件的 Release 的发布时间。
- raw.githubusercontent.com 之类的原始文件链接 GitHub 不统计下载，这类插件保持原样。
- 只按文本替换这两个数字，不重新排版整个文件（其他插件、缩进、换行都不动）；写之前校验 JSON。

用法：python update_download_counts.py [pluginmaster.json] [--dry-run]
环境变量 GITHUB_TOKEN（可选）：带上可以避开匿名访问每小时 60 次的限制。
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime

RELEASE_LINK = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/releases/(?:latest/download|download/[^/]+)/([^/?#]+)$")


def api(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "pluginmaster-download-counts"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def release_stats(owner: str, repo: str, asset: str) -> tuple[int, int] | None:
    """所有 Release 里名为 asset 的文件下载次数之和，和最新一个带它的 Release 的发布时间（Unix 秒）。没有这个文件时 None。"""
    total, latest, found = 0, 0, False
    page = 1
    while True:
        releases = api(f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=100&page={page}")
        for rel in releases:
            for a in rel.get("assets", []):
                if a["name"] == asset:
                    found = True
                    total += int(a["download_count"])
                    if rel.get("published_at"):
                        ts = int(datetime.fromisoformat(rel["published_at"].replace("Z", "+00:00")).timestamp())
                        latest = max(latest, ts)
        if len(releases) < 100:
            break
        page += 1
    return (total, latest) if found else None


def object_spans(text: str) -> list[tuple[int, int]]:
    """顶层数组里每个对象在文本里的起止位置（跳过字符串里的括号）。"""
    spans, depth, start, in_str, esc = [], 0, -1, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
            if depth == 1:
                start = i
        elif ch == "}":
            depth -= 1
            if depth == 0:
                spans.append((start, i + 1))
    return spans


def set_number(block: str, key: str, value: int) -> str:
    pattern = re.compile(r'("' + key + r'"\s*:\s*)(?:"?-?\d+"?)')
    if not pattern.search(block):
        return block
    return pattern.sub(lambda m: m.group(1) + str(value), block, count=1)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    path = args[0] if args else "pluginmaster.json"
    raw = open(path, encoding="utf-8", newline="").read()
    plugins = json.loads(raw)
    spans = object_spans(raw)
    assert len(spans) == len(plugins), f"对象个数对不上：{len(spans)} != {len(plugins)}"

    out, pos, changed = [], 0, False
    for (start, end), plugin in zip(spans, plugins):
        block = raw[start:end]
        m = RELEASE_LINK.match(plugin.get("DownloadLinkInstall") or "")
        stats = release_stats(*m.groups()) if m else None
        name = plugin.get("InternalName")
        if stats is None:
            print(f"{name}: 跳过（{'不是 GitHub Release 链接' if not m else 'Release 里找不到这个文件'}）")
        else:
            count, updated = stats
            print(f"{name}: DownloadCount {plugin.get('DownloadCount')} -> {count}, LastUpdate {plugin.get('LastUpdate')} -> {updated}")
            new = set_number(block, "DownloadCount", count)
            if updated:
                new = set_number(new, "LastUpdate", updated)
            changed |= new != block
            block = new
        out.append(raw[pos:start])
        out.append(block)
        pos = end
    out.append(raw[pos:])
    text = "".join(out)
    json.loads(text)   # 校验：坏了就不写
    if changed and not dry:
        open(path, "w", encoding="utf-8", newline="").write(text)
    print("已更新" if changed and not dry else ("（试跑，没写文件）" if dry else "没有变化"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
