#!/usr/bin/env python3
"""검토 지적(review.json)을 받아 기존 TC를 보완한 새 버전을 만든다. 원본은 그대로 둔다.

사용법:
  python3 revise_test_cases.py <기존 test-cases.json> <revision.json> <출력 폴더> [--review <review.json>] [--config 설정.json]
출력: <출력 폴더>/test-cases.json(새 버전), <출력 폴더>/changes.md
      그다음 render_test_cases.py로 같은 폴더에 시트·HTML을 만든다.

revision.json 의 changes[] (각 항목에 finding = 검토 지적 번호, why = 이유):
  {"op": "add",    "case": {...}}                          새 TC. id를 비우면 다음 번호를 붙인다
  {"op": "update", "id": "TC-030", "fields": {...}}         번호는 그대로, 준 필드만 바꾼다
  {"op": "retire", "id": "TC-033", "replaced_by": [...]}    지우지 않고 retired로 옮긴다
  {"op": "not_covered", "req_id": "REQ-18", "reason": "..."} 제외 사유 추가·수정 (reason을 비우면 제외 목록에서 뺀다)
  {"op": "wait"}                                           답이 와야 할 수 있음 — 아무것도 바꾸지 않고 기록만
  {"op": "skip", "decided_by": "QA 매니저"}                 사람이 반영하지 않기로 정함 — 누가 정했는지 필수
  {"op": "dispute"}                                        TC 작성가가 지적이 타당하지 않다고 봄 — why에 근거(TC 번호·문서 위치)
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
OPS = ("add", "update", "retire", "not_covered", "wait", "skip", "dispute")
# 이 필드가 바뀌면 이미 실행한 결과는 바뀌기 전 내용 기준이므로 다시 실행해야 한다
RERUN_FIELDS = {"title", "preconditions", "steps", "expected"}


def load_config(path):
    base = json.loads((PLUGIN / "defaults.json").read_text(encoding="utf-8"))
    if path and Path(path).exists():
        user = json.loads(Path(path).read_text(encoding="utf-8"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k].update(v)
            else:
                base[k] = v
    return base


def next_id(prefix, used):
    """폐기된 번호까지 포함해 가장 큰 번호 다음. 자릿수는 기존 번호를 따른다(기본 3자리)."""
    found = [m.group(1) for m in (re.match(rf"{re.escape(prefix)}-(\d+)$", i) for i in used) if m]
    width = max([len(n) for n in found] or [3])
    return f"{prefix}-{max([int(n) for n in found] or [0]) + 1:0{width}d}"


def revise(base_path, rev_path, outdir, review_path, cfg):
    doc = json.loads(Path(base_path).read_text(encoding="utf-8"))
    rev = json.loads(Path(rev_path).read_text(encoding="utf-8"))
    cases = doc["cases"]
    retired = doc.setdefault("retired", [])
    by_id = {c["id"]: c for c in cases}
    used = set(by_id) | {c["id"] for c in retired}
    version = doc.get("version", 1) + 1
    prefix = cfg["tc"].get("id_prefix", "TC")
    errs, log, rerun, waiting, skipped, disputed = [], [], [], [], [], []

    for i, ch in enumerate(rev.get("changes", [])):
        where = f"changes[{i}]"
        op, finding, why = ch.get("op"), ch.get("finding", ""), ch.get("why", "")
        if op not in OPS:
            errs.append(f"{where}.op는 {'/'.join(OPS)} 중 하나")
            continue
        if not why:
            errs.append(f"{where}.why(이유)가 없음")
        entry = {"op": op, "finding": finding, "why": why}
        if op == "add":
            case = dict(ch.get("case") or {})
            cid = case.get("id") or next_id(prefix, used)
            if cid in used:
                errs.append(f"{where}: {cid}는 이미 있는 번호(폐기된 번호도 다시 쓰지 않음)")
                continue
            case["id"] = cid
            case["added_in"] = version
            cases.append(case)
            by_id[cid] = case
            used.add(cid)
            entry["id"] = cid
        elif op == "update":
            cid, fields = ch.get("id"), ch.get("fields") or {}
            if cid not in by_id:
                errs.append(f"{where}: {cid}가 없음" + (" (폐기된 TC)" if cid in used else ""))
                continue
            if not fields or "id" in fields:
                errs.append(f"{where}: fields가 비었거나 id를 바꾸려 함(번호는 바꾸지 않는다)")
                continue
            c = by_id[cid]
            before = {k: c.get(k) for k in fields}
            c.update(fields)
            c.setdefault("revisions", []).append({"version": version, "finding": finding, "fields": sorted(fields), "why": why})
            entry.update(id=cid, fields=sorted(fields), before=before)
            if RERUN_FIELDS & set(fields):
                rerun.append(cid)
        elif op == "retire":
            cid = ch.get("id")
            if cid not in by_id:
                errs.append(f"{where}: {cid}가 없음")
                continue
            c = by_id.pop(cid)
            cases.remove(c)
            c["retired"] = {"version": version, "finding": finding, "why": why, "replaced_by": ch.get("replaced_by", [])}
            retired.append(c)
            entry.update(id=cid, replaced_by=ch.get("replaced_by", []))
        elif op == "not_covered":
            rid, reason = ch.get("req_id"), ch.get("reason", "")
            if not rid:
                errs.append(f"{where}.req_id가 없음")
                continue
            nc = doc.setdefault("not_covered", [])
            cur = next((x for x in nc if x.get("req_id") == rid), None)
            entry.update(req_id=rid, before=cur.get("reason") if cur else None, reason=reason)
            if reason:
                if cur:
                    cur["reason"] = reason
                else:
                    nc.append({"req_id": rid, "reason": reason})
            elif cur:
                nc.remove(cur)
        elif op == "skip":
            if not ch.get("decided_by"):
                errs.append(f"{where}.decided_by(반영하지 않기로 정한 사람)가 없음 — TC 작성가 혼자 빼지 않는다(타당하지 않으면 dispute)")
                continue
            entry["decided_by"] = ch["decided_by"]
            skipped.append({"finding": finding, "why": why, "decided_by": ch["decided_by"]})
        elif op == "dispute":
            disputed.append({"finding": finding, "why": why})
        else:
            waiting.append({"finding": finding, "why": why})
        if op in ("skip", "dispute", "wait") and not finding:
            errs.append(f"{where}.finding(지적 번호)이 없음")
        log.append(entry)

    for ch in rev.get("changes", []):
        for rid in ch.get("replaced_by", []):
            if rid not in by_id:
                errs.append(f"{ch.get('id')}의 replaced_by {rid}가 새 버전에 없음")

    review = None
    if review_path:
        review = json.loads(Path(review_path).read_text(encoding="utf-8"))
        mine = [f for f in review.get("findings", []) if f.get("owner") == "tc-writer"]
        done = {e["finding"] for e in log}
        known = {f["id"] for f in review.get("findings", [])}
        errs += [f"{e['finding']}는 검토 지적에 없는 번호" for e in log if e["finding"] and e["finding"] not in known]
        errs += [f"검토 지적 {f['id']}(TC 작성가 담당)을 처리하지 않음 — change나 wait로 남길 것" for f in mine if f["id"] not in done]
    if errs:
        sys.exit("보완 점검 실패:\n  - " + "\n  - ".join(errs))

    doc["version"] = version
    doc.setdefault("revision_log", []).append({
        "version": version, "date": datetime.now().astimezone().isoformat(timespec="seconds"),
        "base": str(base_path), "review": str(review_path or ""), "changes": log, "rerun": rerun, "waiting": waiting,
        "skipped": skipped, "disputed": disputed,
    })
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "test-cases.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    label = {"add": "추가", "update": "수정", "retire": "폐기", "not_covered": "제외 사유", "wait": "답 대기",
             "skip": "반영 안 함(결정)", "dispute": "이견"}
    md = [f"# TC 보완 v{version - 1} → v{version}", "",
          f"- 원본: `{base_path}`" + (f" · 검토: `{review_path}`" if review_path else ""),
          f"- 추가 {sum(e['op'] == 'add' for e in log)} · 수정 {sum(e['op'] == 'update' for e in log)} · 폐기 {sum(e['op'] == 'retire' for e in log)}"
          f" · 제외 사유 {sum(e['op'] == 'not_covered' for e in log)} · 답 대기 {len(waiting)}"
          f" · 반영 안 함 {len(skipped)} · 이견 {len(disputed)} → TC {len(cases)}개", ""]
    md += ["## 지적별 처리", "", "| 지적 | 처리 | 대상 | 이유 |", "|---|---|---|---|"]
    for e in log:
        target = e.get("id") or e.get("req_id") or "-"
        if e["op"] == "update":
            target += f" ({', '.join(e['fields'])})"
        if e["op"] == "retire" and e.get("replaced_by"):
            target += f" → {', '.join(e['replaced_by'])}"
        md.append(f"| {e['finding'] or '-'} | {label[e['op']]} | {target} | {e['why']} |")
    if rerun:
        md += ["", "## 다시 실행할 TC", "", "이미 실행한 결과가 있으면 바뀌기 전 내용 기준이므로 다시 실행한다: " + ", ".join(rerun)]
    added = [e["id"] for e in log if e["op"] == "add"]
    if added:
        md += ["", "## 새로 실행할 TC", "", ", ".join(added)]
    if waiting:
        md += ["", "## 답을 기다리는 지적", ""] + [f"- {w['finding']}: {w['why']}" for w in waiting]
    if skipped:
        md += ["", "## 반영하지 않기로 한 지적", ""] + [f"- {x['finding']}: {x['why']} (결정: {x['decided_by']})" for x in skipped]
    if disputed:
        md += ["", "## TC 작성가 이견 (검토자 재검토에서 답함)", ""] + [f"- {x['finding']}: {x['why']}" for x in disputed]
    (out / "changes.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"v{version}: 추가 {len(added)} · 수정 {sum(e['op'] == 'update' for e in log)} · 폐기 {sum(e['op'] == 'retire' for e in log)}"
          f" · 답 대기 {len(waiting)} · 반영 안 함 {len(skipped)} · 이견 {len(disputed)} · 다시 실행 {len(rerun)}"
          f" → {out / 'test-cases.json'}, {out / 'changes.md'}")


def main():
    args = sys.argv[1:]
    opt = {}
    for flag in ("--review", "--config"):
        if flag in args:
            i = args.index(flag)
            opt[flag] = args[i + 1]
            del args[i:i + 2]
    if len(args) != 3:
        sys.exit(__doc__)
    revise(args[0], args[1], args[2], opt.get("--review"), load_config(opt.get("--config")))


if __name__ == "__main__":
    main()
