#!/usr/bin/env python3
"""신규 기능 검토자 도구: 기계적으로 셀 수 있는 빈틈을 모으고(collect), 검토 결과를 보고서로 그린다(render).

사용법:
  python3 review.py collect --stage tc --analysis <plan-analysis.json> --tc <test-cases.json> --out <폴더>
  python3 review.py collect --stage result --analysis <plan-analysis.json> --tc <test-cases.json>
                            --sheet <결과 시트.xlsx|.csv> [--results <results.json>] [--plan <plan.json>]
                            [--bugs <bugs 폴더> ...] --out <폴더>
      → <폴더>/review-facts.json  (요구사항별 커버리지·실행 결과·후보 지적사항 checks)
      재검토: --previous <이전 review.json> 을 주면 이전 지적마다 TC 보완 기록(revision_log)을 붙여 previous[]에 담는다
  python3 review.py render <review.json> [출력 폴더]
      → review.html, review.md  (review.json = review-facts.json + 검토자가 쓴 summary·findings)

공통 옵션: --config <qa-orchestra.config.json>
"""
import argparse
import csv
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
SEVERITIES = ["high", "mid", "low"]
SEV_LABEL = {"high": "상", "mid": "중", "low": "하"}
STAGE_LABEL = {"tc": "실행 전 TC 검토", "result": "실행 결과 검토"}
STATUS_LABEL = {
    "tc": {"ready": "실행 시작 가능", "needs_work": "보완 후 실행"},
    "result": {"ready": "판정관에게 넘김", "needs_work": "보완 후 판정"},
}
REQ_STATUS = {
    "ok": ("충분", "green"), "weak": ("약함", "amber"), "missing": ("TC 없음", "red"),
    "excluded": ("제외", "muted"), "failing": ("Fail 있음", "red"), "unverified": ("확인 덜 됨", "amber"),
}
HOLD, ERROR = "[판정 보류]", "[자동화 오류]"
RESOLUTION = {"resolved": ("해결", "green"), "partial": ("일부 해결", "amber"), "waiting": ("답 대기", "amber"), "open": ("남음", "red"),
              "skipped": ("반영 안 함(결정)", "muted"), "withdrawn": ("지적 철회", "muted")}


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


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── 시트 읽기 (표준 라이브러리만) ──
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def col_index(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        sheet = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))[0]
        rows = []
        for r in ET.fromstring(z.read(sheet)).iter(f"{{{NS['m']}}}row"):
            vals = {}
            for c in r.findall("m:c", NS):
                t, v = c.get("t"), c.find("m:v", NS)
                if t == "s" and v is not None:
                    val = shared[int(v.text)]
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
                else:
                    val = v.text if v is not None else ""
                vals[col_index(c.get("r"))] = val or ""
            rows.append([vals.get(i, "") for i in range(max(vals) + 1)] if vals else [])
        return rows


def read_sheet(path):
    if str(path).lower().endswith(".csv"):
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.reader(f))
    return read_xlsx(path)


def sheet_results(path, cfg):
    """시트의 결과·이슈내용 열 → {TC: {"result": pass|fail|hold|error|blank, "issue": ...}}"""
    labels = {c["key"]: c["label"] for c in cfg["tc"]["columns"]}
    rows = read_sheet(path)
    head_i = next(i for i, r in enumerate(rows) if labels["id"] in r)
    head = rows[head_i]
    col = {k: head.index(v) for k, v in labels.items() if v in head}
    for k in ("id", "result", "issue"):
        if k not in col:
            sys.exit(f"시트에 '{labels[k]}' 열이 없음: {path}")
    pass_v, fail_v = cfg["tc"]["results"]["pass"].lower(), cfg["tc"]["results"]["fail"].lower()
    out = {}
    for r in rows[head_i + 1:]:
        cell = lambda k: (r[col[k]] if col[k] < len(r) else "").strip()
        tid = cell("id")
        if not tid:
            continue
        res, issue = cell("result").lower(), cell("issue")
        if res == pass_v:
            kind = "pass"
        elif res == fail_v:
            kind = "fail"
        elif issue.startswith(HOLD):
            kind = "hold"
        elif issue.startswith(ERROR):
            kind = "error"
        else:
            kind = "blank"
        out[tid] = {"result": kind, "issue": issue}
    return out


def load_bugs(dirs):
    """bugs 폴더들의 버그(초안·등록) → [{id, tc_id, key, url, severity, summary}]"""
    bugs = {}
    for d in dirs or []:
        d = Path(d)
        for f in sorted(d.glob("*.json")):
            if f.name == "registry.json" or f.name.endswith(".jira.json"):
                continue
            try:
                b = read_json(f)
            except ValueError:
                continue
            if "id" not in b:
                continue
            tc = b.get("tc_id") or (b.get("source") or {}).get("tc_id")
            jira = b.get("jira") or {}
            bugs[(str(d), b["id"])] = {
                "id": b["id"], "tc_id": tc, "key": jira.get("key") or b.get("key"), "url": jira.get("url") or b.get("url"),
                "severity": b.get("severity"), "summary": b.get("summary"), "folder": str(d),
            }
        reg = d / "registry.json"
        if reg.exists():
            for b in read_json(reg).get("bugs", []):
                cur = bugs.setdefault((str(d), b["id"]), {"id": b["id"], "folder": str(d)})
                for k in ("tc_id", "key", "url", "severity", "summary"):
                    if b.get(k):
                        cur[k] = b[k]
    return list(bugs.values())


# ── collect ──
REF = re.compile(r"\b(?:C\d+|REQ-\d+|Q\d+)\b")


def has_number(text):
    return bool(re.search(r"\d", re.sub(r"REQ-\d+|C\d+|Q\d+", "", text)))


def check(out, code, severity, targets, detail, owner):
    out.append({"code": code, "severity": severity, "targets": targets, "detail": detail, "owner": owner})


def collect(args, cfg):
    rc = cfg["review"]
    analysis = read_json(args.analysis) if args.analysis else {}
    tcdoc = read_json(args.tc)
    cases = tcdoc["cases"] if isinstance(tcdoc, dict) else tcdoc
    reqs = analysis.get("requirements", [])
    not_covered = {n.get("req_id"): n.get("reason", "") for n in (tcdoc.get("not_covered", []) if isinstance(tcdoc, dict) else [])}
    by_req = {}
    for c in cases:
        for rid in c.get("req_ids") or []:
            by_req.setdefault(rid, []).append(c)

    status = {}
    plan_modes = {}
    bugs = []
    if args.stage == "result":
        if not args.sheet:
            sys.exit("--stage result 에는 --sheet(결과가 적힌 TC 시트)가 필요함")
        status = sheet_results(args.sheet, cfg)
        if args.results:
            for tid, r in read_json(args.results).get("results", {}).items():
                if tid in status and r.get("message") and status[tid]["result"] in ("hold", "error", "fail"):
                    status[tid]["message"] = r["message"]
        if args.plan:
            plan_modes = {c["id"]: c for c in read_json(args.plan).get("cases", [])}
        bugs = load_bugs(args.bugs)
    bugs_by_tc = {}
    for b in bugs:
        if b.get("tc_id"):
            bugs_by_tc.setdefault(b["tc_id"], []).append(b)

    checks = []
    high_risk_refs = set()
    for r in analysis.get("risks", []):
        if r.get("level") == "high":
            high_risk_refs.update(REF.findall(f"{r.get('area', '')} {r.get('reason', '')}"))
    conflict_reqs = {c["id"]: c.get("req_ids", []) for c in analysis.get("conflicts", [])}

    # 요구사항별 커버리지
    coverage = []
    for q in reqs:
        rid = q["id"]
        tcs = by_req.get(rid, [])
        techs = sorted({t for c in tcs for t in c.get("techniques", [])})
        red = any(f.get("level") == "red" for f in q.get("flags", []))
        hot = red or rid in high_risk_refs or any(rid in conflict_reqs.get(x, []) for x in high_risk_refs)
        row = {"req_id": rid, "group": q.get("group", ""), "keyword": q.get("keyword", ""), "text": q.get("text", ""),
               "flags": q.get("flags", []), "tcs": [c["id"] for c in tcs], "techniques": techs}
        if not tcs:
            if rid in not_covered:
                row["status"] = "excluded"
                row["note"] = not_covered[rid]
                check(checks, "REQ_EXCLUDED", "low", [rid], f"TC 없이 제외됨: {not_covered[rid]}", "pm")
            else:
                row["status"] = "missing"
                check(checks, "REQ_NO_TC", "high" if hot else "mid", [rid], f"{q.get('keyword', rid)} 요구사항을 확인하는 TC가 없음", "tc-writer")
        elif has_number(q.get("text", "")) and rc["boundary_technique"] not in techs:
            row["status"] = "weak"
            check(checks, "REQ_NO_BOUNDARY", "high" if hot else "mid", [rid] + row["tcs"][:3],
                  f"수치 조건이 있는데 {rc['boundary_technique']} TC가 없음", "tc-writer")
        else:
            row["status"] = "ok"
        if args.stage == "result" and tcs:
            counts = {}
            for c in tcs:
                k = status.get(c["id"], {}).get("result", "blank")
                counts[k] = counts.get(k, 0) + 1
            row["results"] = counts
            if row["status"] in ("ok", "weak"):
                if counts.get("fail"):
                    row["status"] = "failing"
                elif any(counts.get(k) for k in ("hold", "error", "blank")):
                    row["status"] = "unverified"
        coverage.append(row)

    # 충돌 → TC
    conflicts = []
    for c in analysis.get("conflicts", []):
        tcs = sorted({t["id"] for rid in c.get("req_ids", []) for t in by_req.get(rid, [])})
        conflicts.append({"id": c["id"], "topic": c.get("topic", ""), "req_ids": c.get("req_ids", []), "tcs": tcs,
                          "verdict": (c.get("verdict") or {}).get("label", "")})
        if not tcs:
            check(checks, "CONFLICT_NO_TC", "high", [c["id"]] + c.get("req_ids", []), f"충돌 '{c.get('topic', '')}'를 확인하는 TC가 없음", "tc-writer")

    # 위험 → TC (위험 설명에 적힌 C·REQ 번호로 연결)
    risks = []
    for r in analysis.get("risks", []):
        refs = REF.findall(f"{r.get('area', '')} {r.get('reason', '')}")
        req_ids = {x for x in refs if x.startswith("REQ-")}
        for x in refs:
            req_ids.update(conflict_reqs.get(x, []))
        tcs = sorted({t["id"] for rid in req_ids for t in by_req.get(rid, [])})
        matched_by = "refs" if tcs else ""
        if not tcs:
            # 번호가 없으면 위험 영역 이름의 낱말(2자 이상)이 TC 구역·제목·기법에 모두 들어 있는 TC로 잇는다
            words = [w for w in re.split(r"[^\w]+", re.sub(r"\(.*?\)", "", r.get("area", ""))) if len(w) >= 2]
            tcs = sorted(c["id"] for c in cases if words and all(
                w in f"{c.get('section', '')} {c.get('title', '')} {' '.join(c.get('techniques', []))}" for w in words))
            matched_by = "keyword" if tcs else ""
        risks.append({"area": r.get("area", ""), "level": r.get("level", ""), "reason": r.get("reason", ""), "refs": refs,
                      "tcs": tcs, "matched_by": matched_by})
        if not tcs and r.get("level") in ("high", "mid"):
            check(checks, "RISK_NO_TC", "high" if r.get("level") == "high" else "mid", refs or [r.get("area", "")],
                  f"위험 '{r.get('area', '')}'에 연결된 TC를 찾지 못함(번호·낱말 기준 — 실제로 덮는 TC가 있는지 확인)", "tc-writer")

    # 영향 범위(직접) → 리그레이션 TC
    impacts = []
    regs = [c for c in cases if c.get("impact")]
    for g in analysis.get("impacts", []):
        for it in g.get("items", []):
            if it.get("level") != "direct":
                continue
            name = it.get("name", "")
            key = re.sub(r"\(.*?\)", "", name).strip()
            tcs = [c["id"] for c in regs if key and (key in c["impact"] or c["impact"] in name)]
            impacts.append({"group": g.get("group", ""), "name": name, "tcs": tcs})
            if not tcs:
                check(checks, "IMPACT_NO_REGRESSION", "mid", [name], f"직접 영향 기능 '{name}'의 리그레이션 TC가 없음", "tc-writer")

    # 질문 → 가정에 기댄 TC / 판정 보류 TC
    questions = []
    hold_by_q = {}
    for tid, s in status.items():
        if s["result"] == "hold":
            for qid in set(re.findall(r"\bQ\d+\b", s.get("message") or s["issue"])):
                hold_by_q.setdefault(qid, []).append(tid)
    for q in analysis.get("questions", []):
        dep = [c["id"] for c in cases if q["id"] in (c.get("assumptions") or [])]
        held = sorted(hold_by_q.get(q["id"], []))
        questions.append({"id": q["id"], "question": q.get("question", ""), "ask_to": q.get("ask_to", ""),
                          "assumption": q.get("assumption", ""), "tcs_depending": dep, "hold_tcs": held})
        if held:
            check(checks, "QUESTION_BLOCKS_JUDGEMENT", "high", [q["id"]] + held, f"답이 없어 {len(held)}개 TC가 판정 보류", "pm")
        passed = [t for t in dep if status.get(t, {}).get("result") == "pass"]
        if passed:
            check(checks, "PASS_ON_ASSUMPTION", "mid", [q["id"]] + passed,
                  f"Pass {len(passed)}개가 임시 가정 기준 — 답이 가정과 다르면 다시 판정해야 함", "pm")
        elif dep and args.stage == "tc":
            check(checks, "QUESTION_OPEN", "mid" if len(dep) >= 3 else "low", [q["id"]],
                  f"임시 가정에 기댄 TC {len(dep)}개 — 답이 다르면 기대 결과가 바뀜", "pm")

    # TC 하나하나
    vague = rc["vague_words"]
    titles = {}
    for c in cases:
        tid = c["id"]
        exp = c.get("expected") or []
        exp_text = " ".join(exp) if isinstance(exp, list) else str(exp)
        if not exp_text.strip():
            check(checks, "TC_NO_EXPECTED", "high", [tid], "기대 결과가 비어 있음", "tc-writer")
        else:
            for line in (exp if isinstance(exp, list) else [exp]):
                hit = [w.strip() for w in vague if w in line]
                if hit and not re.search(r"\d", line):
                    check(checks, "TC_VAGUE_EXPECTED", "mid", [tid], f"기대 결과에 구체적인 값·상태가 없음: \"{line}\"", "tc-writer")
                    break
        if not c.get("steps"):
            check(checks, "TC_NO_STEPS", "mid", [tid], "절차가 비어 있음", "tc-writer")
        if not c.get("priority"):
            check(checks, "TC_NO_PRIORITY", "low", [tid], "우선순위가 비어 있음", "tc-writer")
        if not c.get("req_ids") and not c.get("impact"):
            check(checks, "TC_NO_TRACE", "low", [tid], "어느 요구사항·영향 기능을 확인하는지 연결이 없음", "tc-writer")
        titles.setdefault(c.get("title", "").strip(), []).append(tid)
    for t, ids in titles.items():
        if t and len(ids) > 1:
            check(checks, "TC_DUPLICATE_TITLE", "low", ids, f"제목이 같은 TC: \"{t}\"", "tc-writer")
    top = rc["top_priority"]
    share = (sum(1 for c in cases if c.get("priority") == top) / len(cases)) if cases else 0
    if share > rc["top_priority_share_warn"]:
        check(checks, "PRIORITY_INFLATED", "low", [top], f"{top} 비율 {share:.0%} — 우선순위 기준을 다시 볼 것(실행 순서 구분이 흐려짐)", "tc-writer")

    # 실행 결과
    tc_rows = []
    if args.stage == "result":
        prio = {c["id"]: c.get("priority", "") for c in cases}
        titles_by_id = {c["id"]: c.get("title", "") for c in cases}
        for c in cases:
            tid = c["id"]
            s = status.get(tid, {"result": "blank", "issue": ""})
            mode = plan_modes.get(tid, {})
            row = {"id": tid, "title": titles_by_id[tid], "priority": prio[tid], "result": s["result"],
                   "issue": s.get("message") or s["issue"], "mode": mode.get("mode") or c.get("automation", ""),
                   "mode_reason": mode.get("reason", ""),
                   "bugs": [b.get("key") or b["id"] for b in bugs_by_tc.get(tid, [])]}
            tc_rows.append(row)
            if s["result"] == "fail" and not any(b.get("key") for b in bugs_by_tc.get(tid, [])):
                drafts = [b["id"] for b in bugs_by_tc.get(tid, [])]
                check(checks, "FAIL_NO_BUG", "high", [tid] + drafts,
                      "Fail인데 등록된 버그가 없음" + (f"(초안 {', '.join(drafts)}만 있음)" if drafts else ""), "bug-reporter")
            elif s["result"] == "error":
                check(checks, "AUTOMATION_ERROR", "mid", [tid], f"자동화 오류로 결과 없음: {row['issue'][:80]}", "automation")
            elif s["result"] == "blank":
                owner = "automation" if mode.get("mode") == "blocked" else "tester"
                why = (f" ({mode['mode']}: {mode.get('reason', '')})" if mode.get("mode")
                       else f" (TC 자동화 구분: {c['automation']})" if c.get("automation") else "")
                check(checks, "NOT_RUN", "high" if prio[tid] == top else "mid", [tid], f"실행 결과가 없음{why}", owner)
        for b in bugs:
            if b.get("key"):
                sev = (b.get("severity") or "").lower()
                level = "high" if sev in ("blocker", "critical") else "mid" if sev == "major" else "low"
                check(checks, "BUG_OPEN", level, [b["key"], b.get("tc_id") or ""],
                      f"{b.get('severity', '')} 버그 — 수정·재확인 여부 확인 필요: {b.get('summary', '')}", "dev")

    previous = []
    if args.previous:
        prev = read_json(args.previous)
        log = tcdoc.get("revision_log", []) if isinstance(tcdoc, dict) else []
        for f in prev.get("findings", []):
            changes = [dict(c, version=r["version"]) for r in log for c in r.get("changes", []) if c.get("finding") == f["id"]]
            ops = {c["op"] for c in changes}
            hint = ("skipped" if "skip" in ops else "disputed" if "dispute" in ops
                    else "waiting" if ops == {"wait"} else "resolved" if changes else "open")
            previous.append({**{k: f.get(k) for k in ("id", "severity", "category", "targets", "action", "owner")},
                             "changes": changes, "hint": hint})

    stats = {"requirements": len(reqs), "tcs": len(cases), "top_priority_share": round(share, 3),
             "req_status": {}, "results": {}}
    for row in coverage:
        stats["req_status"][row["status"]] = stats["req_status"].get(row["status"], 0) + 1
    for row in tc_rows:
        stats["results"][row["result"]] = stats["results"].get(row["result"], 0) + 1

    feature = (tcdoc.get("feature") or {}).get("name") if isinstance(tcdoc, dict) else None
    facts = {
        "feature": feature or (analysis.get("feature") or {}).get("name", ""),
        "stage": args.stage,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "inputs": {k: v for k, v in {"analysis": args.analysis, "tc": args.tc, "sheet": args.sheet, "results": args.results,
                                     "plan": args.plan, "bugs": args.bugs}.items() if v},
        "stats": stats, "coverage": coverage, "conflicts": conflicts, "risks": risks, "impacts": impacts,
        "questions": questions, "tc_results": tc_rows, "bugs": bugs, "checks": checks,
    }
    if previous:
        facts["previous_review"] = args.previous
        facts["previous"] = previous
        facts["tc_version"] = tcdoc.get("version", 1)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "review-facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    by_sev = {s: sum(1 for c in checks if c["severity"] == s) for s in SEVERITIES}
    print(f"review-facts.json: 요구사항 {len(reqs)} · TC {len(cases)} · 후보 지적 {len(checks)}건 "
          f"(상 {by_sev['high']} · 중 {by_sev['mid']} · 하 {by_sev['low']}) → {out / 'review-facts.json'}")
    if previous:
        print("이전 지적 " + str(len(previous)) + "건: " + " · ".join(
            f"{RESOLUTION[h][0] if h in RESOLUTION else '이견'} 후보 {sum(p['hint'] == h for p in previous)}"
            for h in list(RESOLUTION) + ["disputed"] if any(p["hint"] == h for p in previous)))
    if tc_rows:
        print("실행 결과: " + " · ".join(f"{k} {v}" for k, v in sorted(stats["results"].items())))


# ── render ──
e = lambda s: html.escape(str(s if s is not None else ""))


def validate(rv, cfg):
    errs = []
    owners = cfg["review"]["owners"]
    if rv.get("stage") not in STAGE_LABEL:
        errs.append("stage는 tc 또는 result")
    s = rv.get("summary") or {}
    if s.get("status") not in ("ready", "needs_work"):
        errs.append("summary.status는 ready 또는 needs_work")
    if not s.get("headline"):
        errs.append("summary.headline(한 문장 요약)이 없음")
    ids = set()
    for i, f in enumerate(rv.get("findings", [])):
        where = f"findings[{i}]"
        for k in ("id", "severity", "category", "targets", "detail", "action", "owner"):
            if not f.get(k):
                errs.append(f"{where}.{k} 없음")
        if f.get("severity") not in SEVERITIES:
            errs.append(f"{where}.severity는 {'/'.join(SEVERITIES)}")
        if f.get("owner") not in owners:
            errs.append(f"{where}.owner는 {', '.join(owners)} 중 하나")
        if f.get("id") in ids:
            errs.append(f"{where}.id 중복: {f.get('id')}")
        ids.add(f.get("id"))
    if rv.get("previous"):
        res = {r.get("id"): r for r in rv.get("resolution", [])}
        for p in rv["previous"]:
            r = res.get(p["id"])
            if not r:
                errs.append(f"resolution에 이전 지적 {p['id']}의 처리 결과가 없음")
            elif r.get("status") not in RESOLUTION:
                errs.append(f"resolution {p['id']}.status는 {'/'.join(RESOLUTION)}")
            elif not r.get("note"):
                errs.append(f"resolution {p['id']}.note(확인한 내용)가 없음")
            elif p.get("hint") == "skipped" and r["status"] != "skipped":
                errs.append(f"resolution {p['id']}: 사람이 반영하지 않기로 정한 지적은 skipped로 둔다(다시 올리려면 사람에게 따로 알린다)")
            elif p.get("hint") == "disputed" and r["status"] not in ("withdrawn", "open", "partial"):
                errs.append(f"resolution {p['id']}: 이견이 달린 지적은 withdrawn(받아들임) 또는 open·partial(근거와 함께 유지)로 답한다")
            elif r["status"] in ("skipped", "withdrawn") and any(f.get("id") == p["id"] for f in rv.get("findings", [])):
                errs.append(f"{p['id']}는 {r['status']}인데 findings에 다시 들어 있음")
    for i, d in enumerate(rv.get("dismissed", [])):
        if not d.get("reason"):
            errs.append(f"dismissed[{i}].reason(뺀 이유)이 없음")
    return errs


def sev_badge(s):
    cls = {"high": "red", "mid": "amber", "low": "green"}[s]
    return f'<span class="flag {cls}">{SEV_LABEL[s]}</span>'


def chips(items, cls="chip"):
    return "".join(f'<span class="{cls}">{e(x)}</span>' for x in items)


def result_bar(counts):
    order = [("pass", "Pass", "green"), ("fail", "Fail", "red"), ("hold", "보류", "amber"), ("error", "오류", "amber"), ("blank", "미실행", "muted")]
    total = sum(counts.values()) or 1
    segs = "".join(f'<i class="seg {c}" style="width:{counts.get(k, 0) / total * 100:.1f}%" title="{lbl} {counts.get(k, 0)}"></i>'
                   for k, lbl, c in order if counts.get(k))
    text = " · ".join(f"{lbl} {counts[k]}" for k, lbl, _ in order if counts.get(k))
    return f'<div class="bar">{segs}</div><span class="muted small">{e(text)}</span>'


def render(path, outdir, cfg):
    rv = read_json(path)
    errs = validate(rv, cfg)
    if errs:
        sys.exit("review.json 점검 실패:\n  - " + "\n  - ".join(errs))
    owners = cfg["review"]["owners"]
    stage = rv["stage"]
    st = rv["stats"]
    findings = sorted(rv.get("findings", []), key=lambda f: (SEVERITIES.index(f["severity"]), f["id"]))
    n_sev = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}
    summary = rv["summary"]
    status_text = STATUS_LABEL[stage][summary["status"]]
    cleared_weak = {t for d in rv.get("dismissed", []) if d.get("code") == "REQ_NO_BOUNDARY" for t in d.get("targets", [])}
    rs = {}
    for c in rv.get("coverage", []):
        k = "ok" if c["status"] == "weak" and c["req_id"] in cleared_weak else c["status"]
        rs[k] = rs.get(k, 0) + 1

    if stage == "tc":
        kpis = [("요구사항", st["requirements"], ""), ("TC", st["tcs"], ""), ("TC 없음", rs.get("missing", 0), "warn" if rs.get("missing") else ""),
                ("커버 약함", rs.get("weak", 0), "caution" if rs.get("weak") else ""), ("지적 상", n_sev["high"], "warn" if n_sev["high"] else "")]
    else:
        r = st.get("results", {})
        kpis = [("Pass", r.get("pass", 0), ""), ("Fail", r.get("fail", 0), "warn" if r.get("fail") else ""),
                ("판정 보류", r.get("hold", 0), "caution" if r.get("hold") else ""),
                ("미실행·오류", r.get("blank", 0) + r.get("error", 0), "caution" if r.get("blank", 0) + r.get("error", 0) else ""),
                ("지적 상", n_sev["high"], "warn" if n_sev["high"] else "")]
    kpi_html = "".join(f'<div class="kpi {c}"><b>{v}</b><span>{e(k)}</span></div>' for k, v, c in kpis)

    # 보완 요청(담당별)
    by_owner = {}
    for f in findings:
        by_owner.setdefault(f["owner"], []).append(f)
    req_cards = []
    for o in owners:
        if o not in by_owner:
            continue
        items = "".join(f'<li>{sev_badge(f["severity"])} <span class="fid">{e(f["id"])}</span> {e(f["action"])}'
                        f' <span class="muted small">{e(", ".join(f["targets"]))}</span></li>' for f in by_owner[o])
        req_cards.append(f'<div class="card"><h3>{e(owners[o])} <span class="muted small">{len(by_owner[o])}건</span></h3><ul class="reqs">{items}</ul></div>')

    rows = "".join(
        f'<tr><td>{sev_badge(f["severity"])}</td><td class="mono">{e(f["id"])}</td><td>{e(f["category"])}</td>'
        f'<td>{chips(f["targets"], "chip mono")}</td><td>{e(f["detail"])}</td><td>{e(f["action"])}</td><td>{e(owners[f["owner"]])}</td></tr>'
        for f in findings)
    findings_html = (f'<div class="tbl"><table><thead><tr><th>심각도</th><th>번호</th><th>분류</th><th>대상</th><th>내용</th><th>보완</th><th>담당</th></tr></thead>'
                     f'<tbody>{rows}</tbody></table></div>') if findings else '<p class="muted">지적사항 없음</p>'

    # 검토자가 '경계값 부족' 후보를 뺐으면 그 요구사항은 약함으로 표시하지 않는다
    cleared = {t for d in rv.get("dismissed", []) if d.get("code") == "REQ_NO_BOUNDARY" for t in d.get("targets", [])}
    for c in rv.get("coverage", []):
        if c["status"] == "weak" and c["req_id"] in cleared:
            c["status"] = "ok"
    cov_rows = []
    for c in rv.get("coverage", []):
        label, cls = REQ_STATUS.get(c["status"], (c["status"], "muted"))
        extra = f'<td>{result_bar(c["results"])}</td>' if stage == "result" else ""
        note = f'<span class="muted small">{e(c.get("note", ""))}</span>' if c.get("note") else ""
        cov_rows.append(
            f'<tr><td class="mono">{e(c["req_id"])}</td><td><b>{e(c["keyword"])}</b><br><span class="muted small">{e(c["text"])}</span>{note}</td>'
            f'<td class="c mono">{len(c["tcs"])}</td><td>{chips(c["techniques"])}</td>{extra}<td><span class="flag {cls}">{label}</span></td></tr>')
    res_head = "<th>실행 결과</th>" if stage == "result" else ""
    cov_html = (f'<div class="tbl"><table><thead><tr><th>번호</th><th>요구사항</th><th class="c">TC</th><th>기법</th>{res_head}<th>상태</th></tr></thead>'
                f'<tbody>{"".join(cov_rows)}</tbody></table></div>')

    q_rows = []
    for q in rv.get("questions", []):
        if not (q["tcs_depending"] or q["hold_tcs"]):
            continue
        held = f'<span class="flag red">보류 {len(q["hold_tcs"])}</span> {chips(q["hold_tcs"], "chip mono")}' if q["hold_tcs"] else ""
        q_rows.append(f'<tr><td class="mono">{e(q["id"])}</td><td>{e(q["question"])}<br><span class="muted small">물어볼 곳: {e(q["ask_to"])} · 임시 가정: {e(q["assumption"])}</span></td>'
                      f'<td class="c mono">{len(q["tcs_depending"])}</td><td>{held}</td></tr>')
    q_html = (f'<div class="tbl"><table><thead><tr><th>질문</th><th>내용</th><th class="c">가정 의존 TC</th><th>판정 보류</th></tr></thead><tbody>{"".join(q_rows)}</tbody></table></div>'
              if q_rows else '<p class="muted">TC에 영향을 주는 미해결 질문 없음</p>')

    res_section = ""
    if stage == "result":
        lbl = {"fail": ("Fail", "red"), "hold": ("판정 보류", "amber"), "error": ("자동화 오류", "amber"), "blank": ("미실행", "muted")}
        rows = []
        for t in rv.get("tc_results", []):
            if t["result"] == "pass":
                continue
            name, cls = lbl[t["result"]]
            why = t["issue"] or (f'{t["mode"]}: {t["mode_reason"]}' if t["mode"] else "")
            rows.append(f'<tr><td class="mono">{e(t["id"])}</td><td>{e(t["title"])}</td><td class="mono">{e(t["priority"])}</td>'
                        f'<td><span class="flag {cls}">{name}</span></td><td class="pre">{e(why)}</td><td>{chips(t["bugs"], "chip mono")}</td></tr>')
        res_section = (f'<section id="results"><h2><span class="no">05</span>Pass 아닌 TC</h2><div class="tbl"><table><thead><tr><th>TC</th><th>제목</th><th>우선순위</th>'
                       f'<th>결과</th><th>내용·사유</th><th>버그</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>')

    prev_html, prev_md = "", []
    if rv.get("previous"):
        res = {r["id"]: r for r in rv.get("resolution", [])}
        counts = {k: sum(1 for p in rv["previous"] if res[p["id"]]["status"] == k) for k in RESOLUTION}
        rows = []
        for p in rv["previous"]:
            r = res[p["id"]]
            label, cls = RESOLUTION[r["status"]]
            op_label = {"add": "추가", "update": "수정", "retire": "폐기", "not_covered": "제외 사유", "wait": "답 대기", "skip": "반영 안 함", "dispute": "이견"}
            did = "<br>".join(e(f"{op_label.get(c['op'], c['op'])} {c.get('id') or c.get('req_id') or ''}".strip()
                                + (f" — {c['decided_by']}" if c.get("decided_by") else "")
                                + (f": {c['why']}" if c["op"] in ("skip", "dispute") else "")) for c in p["changes"]) or '<span class="muted">처리 기록 없음</span>'
            rows.append(f'<tr><td class="mono">{e(p["id"])}</td><td>{sev_badge(p["severity"])}</td><td>{e(p["action"])}<br><span class="muted small">{e(owners.get(p["owner"], p["owner"]))}</span></td>'
                        f'<td class="mono small">{did}</td><td><span class="flag {cls}">{label}</span></td><td>{e(r["note"])}</td></tr>')
            prev_md.append(f"| {p['id']} | {SEV_LABEL[p['severity']]} | {p['action']} | {label} | {r['note']} |")
        summary_line = " · ".join(f"{RESOLUTION[k][0]} {v}" for k, v in counts.items() if v)
        prev_html = (f'<section id="previous"><h2><span class="no">00</span>이전 지적 처리 <span class="muted small">{e(summary_line)} · TC v{e(rv.get("tc_version", ""))}</span></h2>'
                     f'<div class="tbl"><table><thead><tr><th>지적</th><th>심각도</th><th>보완 요청</th><th>TC 보완 기록</th><th>결과</th><th>확인 내용</th></tr></thead>'
                     f'<tbody>{"".join(rows)}</tbody></table></div></section>')
        prev_md = ["", f"## 이전 지적 처리 ({summary_line})", "", "| 지적 | 심각도 | 보완 요청 | 결과 | 확인 내용 |", "|---|---|---|---|---|"] + prev_md
    dismissed = rv.get("dismissed", [])
    dismissed_html = ""
    if dismissed:
        items = "".join(f'<li><span class="mono">{e(d.get("code", ""))}</span> {chips(d.get("targets", []), "chip mono")} {e(d.get("reason", ""))}</li>'
                        for d in dismissed)
        dismissed_html = (f'<section id="dismissed"><details class="card"><summary>검토 후 지적에서 뺀 자동 점검 후보 {len(dismissed)}건</summary>'
                          f'<ul class="reqs">{items}</ul></details></section>')
    # 사람이 '상' 지적을 반영하지 않기로 했으면 감수한 위험으로 맨 위에 드러낸다
    accepted = []
    if rv.get("previous"):
        res = {r["id"]: r for r in rv.get("resolution", [])}
        for p in rv["previous"]:
            if p["severity"] == "high" and res.get(p["id"], {}).get("status") == "skipped":
                who = next((c.get("decided_by") for c in p["changes"] if c.get("decided_by")), "")
                accepted.append(f"감수한 위험: {p['id']} {p['action']} — 반영 안 함(결정: {who})")
    summary = dict(summary, notes=accepted + summary.get("notes", []))
    notes = "".join(f"<li>{e(n)}</li>" for n in summary.get("notes", []))
    inputs = " · ".join(f"{k}: {Path(str(v[0] if isinstance(v, list) else v)).name}" for k, v in rv.get("inputs", {}).items())
    css = (HERE / "templates" / "review.css").read_text(encoding="utf-8")
    title = f'{rv.get("feature", "")} {STAGE_LABEL[stage]}'
    doc = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title><style>{css}</style></head>
<body><div class="wrap">
<header class="top"><span class="eyebrow">QA 오케스트라 · 신규 기능 검토자</span><h1>{e(title)}</h1>
<div class="meta"><span>{e(rv.get("generated_at", "")[:16].replace("T", " "))}</span><span>{e(inputs)}</span></div></header>
<div class="banner {'ok' if summary['status'] == 'ready' else 'warn'}"><b>{e(status_text)}</b><span>{e(summary['headline'])}</span>{f'<ul>{notes}</ul>' if notes else ''}</div>
<div class="kpis">{kpi_html}</div>
<nav class="toc">{'<a href="#previous">이전 지적 처리</a>' if prev_html else ''}<a href="#requests">보완 요청</a><a href="#findings">지적사항</a><a href="#coverage">요구사항 커버리지</a><a href="#questions">미해결 질문</a>{'<a href="#results">Pass 아닌 TC</a>' if stage == 'result' else ''}</nav>
{prev_html}
<section id="requests"><h2><span class="no">01</span>보완 요청 (담당별)</h2><div class="grid">{''.join(req_cards) or '<p class="muted">보완 요청 없음</p>'}</div></section>
<section id="findings"><h2><span class="no">02</span>지적사항 <span class="muted small">상 {n_sev['high']} · 중 {n_sev['mid']} · 하 {n_sev['low']}</span></h2>{findings_html}</section>
<section id="coverage"><h2><span class="no">03</span>요구사항 커버리지</h2>{cov_html}</section>
<section id="questions"><h2><span class="no">04</span>미해결 질문</h2>{q_html}</section>
{res_section}
{dismissed_html}
</div></body></html>
"""
    out = Path(outdir or Path(path).parent)
    out.mkdir(parents=True, exist_ok=True)
    (out / "review.html").write_text(doc, encoding="utf-8")

    md = [f"# {title}", "", f"**{status_text}** — {summary['headline']}", ""]
    md += [f"- {n}" for n in summary.get("notes", [])]
    md += prev_md
    md += ["", "## 보완 요청 (담당별)"]
    for o in owners:
        if o in by_owner:
            md.append(f"### {owners[o]}")
            md += [f"- [{SEV_LABEL[f['severity']]}] {f['id']} {f['action']} ({', '.join(f['targets'])})" for f in by_owner[o]]
    md += ["", "## 지적사항", "", "| 심각도 | 번호 | 분류 | 대상 | 내용 | 담당 |", "|---|---|---|---|---|---|"]
    md += [f"| {SEV_LABEL[f['severity']]} | {f['id']} | {f['category']} | {', '.join(f['targets'])} | {f['detail']} | {owners[f['owner']]} |" for f in findings]
    md += ["", "## 요구사항 커버리지", "", "| 번호 | 키워드 | TC | 상태 |", "|---|---|---|---|"]
    md += [f"| {c['req_id']} | {c['keyword']} | {len(c['tcs'])} | {REQ_STATUS.get(c['status'], (c['status'],))[0]} |" for c in rv.get("coverage", [])]
    if dismissed:
        md += ["", "## 검토 후 지적에서 뺀 자동 점검 후보"]
        md += [f"- {d.get('code', '')} ({', '.join(d.get('targets', []))}): {d.get('reason', '')}" for d in dismissed]
    (out / "review.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"{STAGE_LABEL[stage]}: {status_text} · 지적 {len(findings)}건(상 {n_sev['high']}) → {out / 'review.html'}, {out / 'review.md'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--stage", choices=["tc", "result"], required=True)
    c.add_argument("--analysis")
    c.add_argument("--tc", required=True)
    c.add_argument("--sheet")
    c.add_argument("--results")
    c.add_argument("--plan")
    c.add_argument("--bugs", nargs="*")
    c.add_argument("--out", required=True)
    c.add_argument("--previous")
    c.add_argument("--config")
    r = sub.add_parser("render")
    r.add_argument("review")
    r.add_argument("outdir", nargs="?")
    r.add_argument("--config")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.cmd == "collect":
        collect(args, cfg)
    else:
        render(args.review, args.outdir, cfg)


if __name__ == "__main__":
    main()
