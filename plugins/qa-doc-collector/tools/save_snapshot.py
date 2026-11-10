#!/usr/bin/env python3
"""가져온 문서 한 건을 입력 폴더에 스냅샷으로 저장한다.

  python3 tools/save_snapshot.py <입력 폴더> --url URL --type plan --title 제목 \
      --connector Notion [--version 2026-11-10T08:04Z] [--author 작성자] < 본문.md

- 같은 URL이 이미 있으면 같은 파일을 덮어쓰고, 내용이 바뀌었으면
  이전본을 .history/ 에 남기고 changes.md 에 diff 를 추가한다.
- manifest.json 에 문서 목록(URL·종류·가져온 시각·버전·해시)을 유지한다.
- 기능 폴더(<입력 폴더>의 상위)에 .gitignore(*)를 만들어 git에 올라가지 않게 한다.
- 마지막 줄에 결과를 JSON 한 줄로 출력한다: status = new | unchanged | changed
"""
import argparse
import datetime as dt
import difflib
import hashlib
import json
import re
import sys
from pathlib import Path

TYPES = ["plan", "policy", "dev", "design", "slack"]
TYPE_LABEL = {"plan": "기획서", "policy": "정책", "dev": "개발", "design": "디자인", "slack": "슬랙"}


def slugify(text, limit=30):
    text = re.sub(r"^\s*\[[^\]]*\]\s*", "", text)  # 제목 앞 [기획서] 같은 표시는 종류 이름과 겹친다
    text = re.sub(r"[\[\]()]", "", text)
    text = re.sub(r"[^\w가-힣.-]+", "_", text).strip("_")
    return text[:limit] or "doc"


def load_manifest(folder):
    path = folder / "manifest.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"docs": []}


def split_front_matter(text):
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:]
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--url", required=True)
    ap.add_argument("--type", required=True, choices=TYPES)
    ap.add_argument("--title", required=True)
    ap.add_argument("--connector", required=True, help="Notion, Slack, paste 등")
    ap.add_argument("--version", default="")
    ap.add_argument("--author", default="")
    args = ap.parse_args()

    body = sys.stdin.read().strip() + "\n"
    if not body.strip():
        sys.exit("본문이 비어 있습니다. 표준 입력으로 문서 본문을 넣어 주세요.")

    folder = Path(args.folder)
    folder.mkdir(parents=True, exist_ok=True)
    # 회사 문서가 사용자 저장소에 커밋되지 않게, 기능 폴더(inputs·analysis의 상위) 전체를 git에서 뺀다
    guard = folder.resolve().parent / ".gitignore"
    if not guard.exists():
        guard.write_text("# qa-orchestra: 가져온 문서와 분석 결과는 커밋하지 않는다\n*\n", encoding="utf-8")
    manifest = load_manifest(folder)
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]

    entry = next((d for d in manifest["docs"] if d["url"] == args.url), None)
    if entry is None:
        n = len(manifest["docs"]) + 1
        name = f"{n:02d}_{TYPE_LABEL[args.type]}_{slugify(args.title)}.md"
        entry = {"file": name, "url": args.url}
        manifest["docs"].append(entry)
        status = "new"
    else:
        status = "unchanged" if entry.get("sha256") == digest else "changed"

    path = folder / entry["file"]
    if status == "changed" and path.exists():
        old_body = split_front_matter(path.read_text(encoding="utf-8"))
        hist = folder / ".history"
        hist.mkdir(exist_ok=True)
        stamp = (entry.get("fetched_at") or now).replace(":", "")[:15]
        (hist / f"{path.stem}.{stamp}.md").write_text(old_body, encoding="utf-8")
        diff = difflib.unified_diff(
            old_body.splitlines(), body.splitlines(),
            fromfile=f"이전 ({entry.get('version') or entry.get('fetched_at')})",
            tofile=f"현재 ({args.version or now})", lineterm="", n=1)
        with open(folder / "changes.md", "a", encoding="utf-8") as f:
            f.write(f"\n## {now} · {args.title}\n원본: {args.url}\n\n```diff\n")
            f.write("\n".join(diff) + "\n```\n")

    entry.update({
        "type": args.type, "title": args.title, "connector": args.connector,
        "version": args.version, "author": args.author, "sha256": digest,
        "fetched_at": now if status != "unchanged" else entry.get("fetched_at", now),
        "checked_at": now,
    })

    front = ["---", f"source_url: {args.url}", f"type: {args.type}", f"title: {args.title}",
             f"connector: {args.connector}", f"version: {args.version}",
             f"fetched_at: {entry['fetched_at']}", f"sha256: {digest}", "---", ""]
    path.write_text("\n".join(front) + body, encoding="utf-8")
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"status": status, "file": str(path), "type": args.type}, ensure_ascii=False))


if __name__ == "__main__":
    main()
