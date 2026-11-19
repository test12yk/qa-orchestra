#!/usr/bin/env python3
"""신규 기능 QA 리포터 도구: 판정서(judgment.json)와 실행 결과 검토(review.json)를 중심으로
기획 분석·TC·버그를 모아 리포트 재료를 만들고(collect), 한 장짜리 리포트를 그리고(render),
컨플루언스 등 바깥에 올린 위치를 기록한다(record).

사용법:
  python3 report.py collect --judgment <judgment.json> --review <실행 결과 review.json>
                            [--analysis <plan-analysis.json>] [--tc <test-cases.json>]
                            [--bug-status QAO-8=해야 할 일 ...] [--tc-sheet-url <결과가 채워진 TC 시트 주소>]
                            [--link judgment=<판정서 링크> review=<검토 보고서 링크> analysis=<분석서 링크> ...] --out <폴더>
      → <폴더>/report-facts.json  (판정서에 결정이 있으면 완료 리포트, 없으면 중간 리포트)
  python3 report.py render <report.json> [출력 폴더]
      → report.html, report.md, report.confluence.html  (report.json = report-facts.json + 리포터가 쓴 summary)
  python3 report.py record <report.json> --where confluence --url <페이지 URL> [--page-id <id>]
      → report.json에 올린 위치를 남기고 다시 그린다(다음에 같은 페이지를 고치는 데 쓴다).

공통 옵션: --config <qa-orchestra.config.json>
"""
import argparse
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

PLUGIN = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
ORDER = ["pass", "fail", "hold", "blank", "error"]
REQ_LABEL = {"ok": ("충족", "green"), "failing": ("Fail 있음", "red"), "unverified": ("확인 덜 됨", "amber"),
             "missing": ("TC 없음", "red"), "weak": ("커버 약함", "amber"), "excluded": ("제외", "muted")}
RES_CLS = {"pass": "green", "fail": "red", "hold": "amber", "blank": "muted", "error": "red"}
MODE = {"api": "API 자동화", "manual": "수동", "수동": "수동", "blocked": "자동화 불가"}
VCLS = {"include": "ok", "conditional": "warn", "hold": "bad"}
REF = re.compile(r"\b(?:E\d|B-\d+|K-\d+|TC-\d+|REQ-\d+|Q\d+|F-\d+|C\d+|[A-Z][A-Z0-9]+-\d+)\b")


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


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def outputs(args, rv, jd, an, tc, bugs, rows):
    """근거 자료의 'QA 산출물': 처음 보는 사람도 알게 단계·이름·내용·보는 곳(링크, 없으면 사람이 읽는 HTML 파일)으로."""
    def viewable(p):
        h = Path(p).with_suffix(".html")
        return str(h) if h.exists() else p
    items = []
    if args.analysis:
        items.append({"role": "analysis", "step": "기획 분석가", "name": "기획 분석서", "file": viewable(args.analysis),
                      "what": f"기획서·정책·개발 문서를 대조해 요구사항 {len(an.get('requirements', []))}개, 문서 간 충돌 {len(an.get('conflicts', []))}건, "
                              f"기획에 물을 질문 {len(an.get('questions', []))}개를 정리"})
    items.append({"role": "tc", "step": "신규 TC 작성가 · 신규 기능 자동화", "name": "테스트케이스와 실행 결과", "file": args.sheet_file or (viewable(args.tc) if args.tc else ""),
                  "url": args.tc_sheet_url,
                  "what": f"TC {len(rows)}개의 사전조건·절차·기대 결과와 실행 결과(Pass/Fail·이슈내용)"})
    keys = [b for b in bugs if b.get("key")]
    if bugs:
        items.append({"role": "bugs", "step": "버그 리포터", "name": "버그 리포트(지라)", "url": jira_search(keys) if keys else None,
                      "what": f"Fail에서 등록한 결함 {len(bugs)}건 — 재현 절차·기대/실제 결과·심각도" + (f" ({', '.join(b['key'] for b in keys)})" if keys else "")})
    items.append({"role": "review", "step": "신규 기능 검토자", "name": "실행 결과 검토 보고서", "file": viewable(args.review),
                  "what": f"미실행·판정 보류·가정 위의 판정·버그 연결을 점검한 보완 요청 {len(rv.get('findings', []))}건"})
    items.append({"role": "judgment", "step": "신규 기능 판정관", "name": "편입 판정서", "file": viewable(args.judgment),
                  "what": f"요소 8가지 근거, 릴리즈 이슈 {len(jd.get('blockers', []))}개와 해결할 일 {len(jd.get('conditions', []))}개, QA 매니저 결정 칸"})
    for it in items:
        if args.link.get(it["role"]):
            it["url"] = args.link[it["role"]]
    return [{k: v for k, v in it.items() if v} for it in items]


def collect(args, cfg):
    rc = cfg["report"]
    jd = read_json(args.judgment)
    rv = read_json(args.review)
    if rv.get("stage") != "result":
        sys.exit("리포트에는 검토자의 '실행 결과 검토'(stage result) review.json이 필요함")
    if not jd.get("recommendation", {}).get("verdict"):
        sys.exit("판정서에 판정관 추천(recommendation)이 없음 — 판정관을 먼저 돌린다")
    an = read_json(args.analysis) if args.analysis else {}
    tc = read_json(args.tc) if args.tc else {}
    types = {c["id"]: c.get("type", "") for c in tc.get("cases", [])}

    rows = rv.get("tc_results", [])
    results = {k: sum(1 for r in rows if r["result"] == k) for k in ORDER}
    by_pri = {}
    for r in rows:
        by_pri.setdefault(r["priority"], {k: 0 for k in ORDER})[r["result"]] += 1
    by_mode = {}
    for r in rows:
        m = MODE.get(r.get("mode") or "", r.get("mode") or "미정")
        by_mode[m] = by_mode.get(m, 0) + 1
    by_type = {}
    for r in rows:
        t = types.get(r["id"])
        if t:
            by_type.setdefault(t, {k: 0 for k in ORDER})[r["result"]] += 1

    # 버그: 같은 버그가 여러 폴더에 있으면 지라 키가 있는 쪽을 쓴다
    bugs, seen = [], {}
    for b in rv.get("bugs", []):
        k = (b.get("id"), b.get("tc_id"))
        if k not in seen or (b.get("key") and not seen[k].get("key")):
            seen[k] = b
    closed = {s.lower() for s in rc["closed_statuses"]}
    for b in seen.values():
        st = args.bug_status.get(b.get("key") or "", "")
        bugs.append({"key": b.get("key"), "id": b.get("id"), "url": b.get("url"), "severity": b.get("severity", ""),
                     "summary": b.get("summary", ""), "tc_id": b.get("tc_id", ""), "status": st,
                     "open": (st or "").lower() not in closed})

    decisions = jd.get("decisions", [])
    rec = jd["recommendation"]
    feat = an.get("feature", {}) if isinstance(an.get("feature"), dict) else {}
    facts = {
        "feature": jd.get("feature") or rv.get("feature", ""),
        "generated_at": now(),
        "status": "final" if decisions else "interim",
        "inputs": {k: v for k, v in {"judgment": args.judgment, "review": args.review, "analysis": args.analysis, "tc": args.tc}.items() if v},
        "about": {k: v for k, v in {"sprint": feat.get("sprint"), "purpose": feat.get("summary", {}).get("purpose"),
                                     "target": feat.get("summary", {}).get("target"), "flow": feat.get("summary", {}).get("flow")}.items() if v},
        "sources": [{"title": s.get("title", ""), "date": s.get("date", "")} for s in an.get("sources", [])],
        "scope": an.get("scope", {}),
        "not_covered": tc.get("not_covered", []),
        "recommendation": {k: rec.get(k) for k in ("verdict", "headline", "reasons", "deviation_reason") if rec.get(k)},
        "decision": decisions[-1] if decisions else None,
        "decisions_seen": len(decisions),
        "blockers": jd.get("blockers", []),
        "conditions": jd.get("conditions", []),
        "alternatives": jd.get("alternatives", []),
        "elements": [{k: el.get(k) for k in ("id", "name", "status", "summary")} for el in jd.get("elements", [])],
        "execution": {"total": len(rows), "results": results, "by_priority": dict(sorted(by_pri.items())),
                      "by_mode": by_mode, "by_type": by_type},
        "not_passed": [{k: r.get(k) for k in ("id", "title", "priority", "result", "issue", "mode", "mode_reason", "bugs")}
                       for r in rows if r["result"] != "pass"],
        "coverage": [{k: c.get(k) for k in ("req_id", "group", "keyword", "text", "status", "results", "tcs")} for c in rv.get("coverage", [])],
        "bugs": bugs,
        "bug_status_checked": bool(args.bug_status),
        "questions": [{k: q.get(k) for k in ("id", "question", "ask_to", "assumption", "tcs_depending", "hold_tcs")} for q in jd.get("questions", [])],
        "impacts": [dict(im, results={t: next((r["result"] for r in rows if r["id"] == t), "") for t in im.get("tcs", [])})
                    for im in rv.get("impacts", [])],
        "review": {"status": rv.get("summary", {}).get("status"), "headline": rv.get("summary", {}).get("headline", ""),
                   "findings": [{k: f.get(k) for k in ("id", "severity", "category", "action", "owner")} for f in rv.get("findings", [])],
                   "accepted_risks": [n for n in rv.get("summary", {}).get("notes", []) if n.startswith("감수한 위험")]},
        "tc_sheet": {"url": args.tc_sheet_url} if args.tc_sheet_url else None,
        "outputs": outputs(args, rv, jd, an, tc, bugs, rows),
        "links": [],
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report-facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    vl = rc["verdicts"]
    kind = f"완료 리포트(결정: {vl[decisions[-1]['decision']]})" if decisions else f"중간 리포트(결정 대기, 추천 {vl[rec['verdict']]})"
    print(f"report-facts.json: {kind} · TC {len(rows)}개 " + " · ".join(f"{rc['results'][k]} {v}" for k, v in results.items() if v)
          + f" · 열린 버그 {sum(b['open'] for b in bugs)}" + ("" if args.bug_status else " (버그 상태 미조회)"))


# ── render ──
e = lambda s: html.escape(str(s if s is not None else ""))


def validate(r, cfg):
    errs = []
    s = r.get("summary") or {}
    if not s.get("headline"):
        errs.append("summary.headline(한 문장 요약)이 없음")
    pts = s.get("points") or []
    if not 2 <= len(pts) <= 6:
        errs.append("summary.points는 2~6개(매니저·기획이 1분 안에 읽을 요점)")
    for i, p in enumerate(pts):
        if not REF.search(p):
            errs.append(f"summary.points[{i}]에 근거 번호(TC·버그·질문·조건·요소 번호 등)가 없음: {p[:40]}")
    if r.get("status") not in ("interim", "final"):
        errs.append("status는 interim/final")
    if r.get("status") == "final" and not r.get("decision"):
        errs.append("완료 리포트(final)인데 사람의 결정(decision)이 없음 — 결정 전이면 interim")
    if r.get("status") == "interim" and r.get("decision"):
        errs.append("결정이 있는데 중간 리포트(interim)로 되어 있음 — collect를 다시 돌린다")
    if r.get("tc_sheet") and not str(r["tc_sheet"].get("url", "")).startswith(("https://", "http://")):
        errs.append("tc_sheet.url은 http(s) 주소(결과가 채워진 TC 시트 — 구글 시트 등)")
    for i, o in enumerate(r.get("outputs", [])):
        if o.get("url") and not str(o["url"]).startswith(("https://", "http://")):
            errs.append(f"outputs[{i}].url은 http(s) 주소")
    for i, l in enumerate(r.get("links", [])):
        if not l.get("label") or not str(l.get("url", "")).startswith(("https://", "http://")):
            errs.append(f"links[{i}]: label과 http(s) url이 필요")
    owners = cfg["report"]["owners"]
    errs += [f"conditions {c.get('id')}: 담당 '{c.get('owner')}'가 설정 owners에 없음" for c in r.get("conditions", []) if c.get("owner") not in owners]
    return errs


def stale_note(r):
    """판정서에 리포트 이후 새 결정이 생겼으면 알린다(리포트를 다시 모아야 함)."""
    p = r.get("inputs", {}).get("judgment")
    if not p or not Path(p).exists():
        return ""
    n = len(read_json(p).get("decisions", []))
    return "" if n == r.get("decisions_seen", 0) else f"판정서에 이 리포트 이후 결정이 {n - r.get('decisions_seen', 0)}건 더 기록됨 — collect를 다시 돌릴 것"


def bar(counts, total):
    if not total:
        return ""
    return '<div class="bar">' + "".join(f'<span class="seg {RES_CLS[k]}" style="width:{counts.get(k, 0) / total * 100:.1f}%"></span>'
                                         for k in ORDER if counts.get(k)) + "</div>"


def jira(b):
    return f'<a href="{e(b["url"])}">{e(b["key"])}</a>' if b.get("url") else e(b.get("key") or b.get("id"))


def jira_search(bugs):
    """열린 버그를 지라에서 한 번에 보는 검색 주소(키가 있고 주소가 같은 사이트일 때)."""
    linked = [b for b in bugs if b.get("key") and b.get("url")]
    if not linked:
        return ""
    u = urlsplit(linked[0]["url"])
    return f"{u.scheme}://{u.netloc}/issues/?jql=" + quote(f"key in ({', '.join(b['key'] for b in linked)}) ORDER BY priority DESC")


def key_bugs(r, cfg):
    """한 줄 요약에 그대로 보일 주요 결함(설정 report.highlight_severities, 심각도 순)."""
    sev = cfg["report"]["highlight_severities"]
    return sorted([b for b in r["bugs"] if b["open"] and b["severity"] in sev], key=lambda b: sev.index(b["severity"]))


def blocker_by_id(r):
    return {b["id"]: b for b in r.get("blockers", [])}


def render(path, outdir, cfg):
    r = read_json(path)
    errs = validate(r, cfg)
    if errs:
        sys.exit("report.json 점검 실패:\n  - " + "\n  - ".join(errs))
    rc = cfg["report"]
    vl, rl, owners = rc["verdicts"], rc["results"], rc["owners"]
    rec, dec, s = r["recommendation"], r.get("decision"), r["summary"]
    final = r["status"] == "final"
    ex = r["execution"]
    res = ex["results"]
    run = res["pass"] + res["fail"]
    open_bugs = [b for b in r["bugs"] if b["open"]]
    sev = " · ".join(f"{k} {sum(1 for b in open_bugs if b['severity'] == k)}" for k in dict.fromkeys(b["severity"] for b in open_bugs))
    bmap = blocker_by_id(r)
    accepted = set((dec or {}).get("conditions_accepted", []))
    stale = stale_note(r)
    kind = "QA 완료 리포트" if final else "중간 리포트 · 결정 대기"
    title = f'{r["feature"]} 신규 기능 QA 리포트'

    # 상태 카드: 결정(있으면) + 판정관 추천
    if dec:
        diff = " (추천과 다름)" if dec["decision"] != rec["verdict"] else ""
        state = (f'<div class="decision {VCLS[dec["decision"]]}"><span class="eyebrow">QA 매니저 결정</span><b>{e(vl[dec["decision"]])}{e(diff)}</b>'
                 f'<span>{e(dec["by"])} · {e(dec["at"][:16].replace("T", " "))}</span>'
                 + (f'<span>{e(dec["note"])}</span>' if dec.get("note") else "")
                 + (f'<span>받아들인 조건: {e(", ".join(dec["conditions_accepted"]))}</span>' if dec.get("conditions_accepted") else "")
                 + f'<span class="muted small">판정관 추천: {e(vl[rec["verdict"]])}</span></div>')
    else:
        state = (f'<div class="decision pending"><span class="eyebrow">QA 매니저 결정</span><b>결정 대기</b>'
                 f'<span>판정관 추천: <b class="inline {VCLS[rec["verdict"]]}">{e(vl[rec["verdict"]])}</b> — {e(rec["headline"])}</span>'
                 '<span class="muted small">결정이 기록되면 이 리포트가 완료 리포트로 바뀐다</span></div>')
    pts = "".join(f"<li>{e(p)}</li>" for p in s["points"])
    kb = key_bugs(r, cfg)
    kb_html = ('<ul class="keybugs">' + "".join(
        f'<li><span class="flag {"red" if b["severity"] in rc["release_blocking_severities"] else "amber"}">{e(b["severity"])}</span> '
        f'<a class="mono" href="{e(b["url"])}">{e(b["key"])}</a> {e(b["summary"])} <span class="muted small">{e(b["status"] or "상태 미조회")}</span></li>'
        if b.get("url") else f'<li><span class="flag amber">{e(b["severity"])}</span> <span class="mono">{e(b.get("key") or b["id"])}</span> {e(b["summary"])}</li>'
        for b in kb) + "</ul>") if kb else ""
    head = f'<div class="verdict"><span class="eyebrow">한 줄 요약</span><p class="lead">{e(s["headline"])}</p>{kb_html}<ul>{pts}</ul></div>'

    kpis = [("TC 실행", f"{run}/{ex['total']}", "" if run == ex["total"] else "caution"),
            ("Pass", res["pass"], ""), ("Fail", res["fail"], "warn" if res["fail"] else ""),
            ("판정 보류·미실행", res["hold"] + res["blank"] + res["error"], "caution" if res["hold"] + res["blank"] + res["error"] else ""),
            ("열린 버그", len(open_bugs), "warn" if open_bugs else "")]
    sheet = (r.get("tc_sheet") or {}).get("url", "")
    href = {"TC 실행": (sheet, "TC 실행 시트 열기"), "열린 버그": (jira_search(open_bugs), "지라에서 열린 버그 보기")}
    kpi_html = '<div class="kpis">' + "".join(
        f'<a class="kpi link {c}" href="{e(href[k][0])}" title="{e(href[k][1])}"><b>{e(v)}</b><span>{e(k)} ↗</span></a>' if href.get(k, ("",))[0]
        else f'<div class="kpi {c}"><b>{e(v)}</b><span>{e(k)}</span></div>' for k, v, c in kpis) + "</div>"
    kpi_sub = (f'<p class="muted small">열린 버그 {e(sev) if sev else "없음"}'
               + (" — " + ", ".join(f'<a href="{e(b["url"])}">{e(b["key"])}</a>' if b.get("url") else e(b.get("key") or b["id"]) for b in open_bugs) if open_bugs else "")
               + ("" if r.get("bug_status_checked") else " · 버그 상태 미조회") + "</p>")

    # 남은 일
    todo_rows = []
    for c in r.get("conditions", []):
        lv = [bmap[b]["level"] for b in c.get("resolves", []) if b in bmap]
        flag = '<span class="flag red">릴리즈 불가</span>' if "block" in lv else '<span class="flag amber">조건부 릴리즈</span>' if lv else ""
        acc = ' <span class="flag green">받아들인 조건</span>' if c["id"] in accepted else ""
        why = "; ".join(f'{b} {bmap[b]["title"]}' for b in c.get("resolves", []) if b in bmap)
        todo_rows.append(f'<tr><td class="mono">{e(c["id"])}</td><td>{e(c["what"])}{acc}<div class="muted small">확인: {e(c["verify"])}</div></td>'
                         f'<td>{e(owners.get(c["owner"], c["owner"]))}</td><td>{flag}<div class="muted small">{e(why)}</div></td></tr>')
    todo_lead = ("받아들인 조건은 편입 뒤에도 끝까지 확인한다." if final and dec["decision"] == "conditional"
                 else "편입하려면 '릴리즈 불가' 이슈를 해결하는 일이 모두 끝나야 한다." if (dec or {}).get("decision", rec["verdict"]) == "hold" else "")
    todo_html = (f'<section id="todo"><h2>남은 일 <span class="muted small">{len(todo_rows)}개 · 담당별</span></h2>'
                 + (f'<p class="muted">{e(todo_lead)}</p>' if todo_lead else "")
                 + f'<div class="tbl"><table><thead><tr><th>번호</th><th>할 일</th><th>담당</th><th>해결하는 이슈</th></tr></thead><tbody>{"".join(todo_rows)}</tbody></table></div></section>') if todo_rows else ""

    q_open = [q for q in r.get("questions", [])]
    q_rows = "".join(f'<tr><td class="mono">{e(q["id"])}</td><td>{e(q["question"])}<div class="muted small">임시 가정: {e(q.get("assumption", ""))}</div></td>'
                     f'<td>{e(q.get("ask_to", ""))}</td><td class="c">{len(q.get("tcs_depending") or [])}'
                     + (f'<div class="muted small">보류 {len(q["hold_tcs"])}</div>' if q.get("hold_tcs") else "") + "</td></tr>" for q in q_open)
    q_html = (f'<section id="questions"><h2>답이 필요한 질문 <span class="muted small">{len(q_open)}개</span></h2>'
              '<p class="muted">답이 임시 가정과 다르면 기대 결과가 바뀌는 TC만 다시 판정한다.</p>'
              f'<div class="tbl"><table><thead><tr><th>번호</th><th>질문</th><th>답할 사람</th><th class="c">걸린 TC</th></tr></thead><tbody>{q_rows}</tbody></table></div></section>') if q_open else ""

    sc = r.get("scope") or {}
    sc_in = "".join(f"<li>{e(x)}</li>" for x in sc.get("in", []))
    sc_out = "".join(f'<li>{e(x["item"] if isinstance(x, dict) else x)}' + (f'<span class="muted small"> — {e(x.get("reason", ""))}</span>' if isinstance(x, dict) and x.get("reason") else "") + "</li>"
                     for x in sc.get("out", []))
    sc_nc = "".join(f'<li>{e(x.get("req_id", ""))} {e(x.get("reason", x.get("text", "")))}</li>' if isinstance(x, dict) else f"<li>{e(x)}</li>" for x in r.get("not_covered", []))
    scope_html = ""
    if sc_in or sc_out or sc_nc:
        scope_html = ('<section id="scope"><h2>테스트 범위</h2><div class="grid">'
                      + (f'<div class="card"><h3>포함</h3><ul class="ev">{sc_in}</ul></div>' if sc_in else "")
                      + (f'<div class="card"><h3>범위 밖</h3><ul class="ev">{sc_out}</ul></div>' if sc_out else "")
                      + (f'<div class="card"><h3>신규 TC로 덮지 않은 요구사항</h3><ul class="ev">{sc_nc}</ul></div>' if sc_nc else "")
                      + "</div></section>")

    # ── 상세 ──
    bug_rows = "".join(f'<tr><td class="mono">{jira(b)}</td><td>{e(b["severity"])}</td><td>{e(b["status"] or "상태 미조회")}</td>'
                       f'<td class="mono">{e(b["tc_id"])}</td><td>{e(b["summary"])}</td></tr>' for b in r["bugs"])
    bug_html = (f'<section id="bugs"><h2>결함 <span class="muted small">{len(r["bugs"])}건 · 열림 {len(open_bugs)}</span></h2>'
                f'<div class="tbl"><table><thead><tr><th>키</th><th>심각도</th><th>상태</th><th>TC</th><th>내용</th></tr></thead><tbody>{bug_rows}</tbody></table></div></section>') if bug_rows else ""

    def count_table(groups, head):
        rows = "".join(f'<tr><td>{e(g)}</td>' + "".join(f'<td class="c mono">{c.get(k, 0) or ""}</td>' for k in ORDER)
                       + f'<td>{bar(c, sum(c.values()))}</td></tr>' for g, c in groups.items())
        return (f'<div class="tbl"><table><thead><tr><th>{e(head)}</th>' + "".join(f'<th class="c">{e(rl[k])}</th>' for k in ORDER)
                + f'<th></th></tr></thead><tbody>{rows}</tbody></table></div>')
    mode = " · ".join(f"{k} {v}" for k, v in ex["by_mode"].items())
    np_rows = "".join(f'<tr><td class="mono">{e(t["id"])}</td><td>{e(t["priority"])}</td><td><span class="flag {RES_CLS[t["result"]]}">{e(rl[t["result"]])}</span></td>'
                      f'<td>{e(t["title"])}<div class="muted small">{e(t.get("issue") or t.get("mode_reason") or ("실행 방식: " + (t.get("mode") or "미정")))}</div></td>'
                      f'<td class="mono">{e(", ".join(x for x in (t.get("bugs") or []) if x))}</td></tr>' for t in r["not_passed"])
    run_html = (f'<section id="run"><h2>실행 결과 <span class="muted small">{ex["total"]}개 · 실행 방식 {e(mode)}</span>'
                + (f'<a class="small" href="{e(sheet)}">TC 실행 시트 열기 ↗</a>' if sheet else "") + '</h2>'
                f'<div class="grid2"><div>{count_table(ex["by_priority"], "우선순위")}</div>'
                + (f'<div>{count_table(ex["by_type"], "유형")}</div>' if ex.get("by_type") else "") + "</div>"
                + (f'<h3>통과하지 못한 TC {len(r["not_passed"])}개</h3><div class="tbl"><table><thead><tr><th>TC</th><th>우선</th><th>결과</th><th>내용</th><th>버그</th></tr></thead><tbody>{np_rows}</tbody></table></div>' if np_rows else "")
                + "</section>")

    cov = r.get("coverage", [])
    cov_cnt = {}
    for c in cov:
        cov_cnt[c["status"]] = cov_cnt.get(c["status"], 0) + 1
    cov_rows = "".join(f'<tr><td class="mono">{e(c["req_id"])}</td><td>{e(c["keyword"])}<div class="muted small">{e(c["text"])}</div></td>'
                       f'<td><span class="flag {REQ_LABEL.get(c["status"], (c["status"], "muted"))[1]}">{e(REQ_LABEL.get(c["status"], (c["status"],))[0])}</span></td>'
                       f'<td class="mono small">{e(" · ".join(f"{rl.get(k, k)} {v}" for k, v in (c.get("results") or {}).items()))}</td></tr>' for c in cov)
    cov_html = (f'<section id="coverage"><h2>요구사항 <span class="muted small">{len(cov)}개 · '
                + e(" · ".join(f"{REQ_LABEL.get(k, (k,))[0]} {v}" for k, v in cov_cnt.items())) + '</span></h2>'
                f'<div class="tbl"><table><thead><tr><th>번호</th><th>요구사항</th><th>상태</th><th>TC 결과</th></tr></thead><tbody>{cov_rows}</tbody></table></div></section>') if cov else ""

    im_rows = "".join(f'<tr><td>{e(im.get("group", ""))}</td><td>{e(im["name"])}</td><td>'
                      + "".join(f'<span class="chip">{e(t)} <span class="{RES_CLS.get(v, "muted")}-t">{e(rl.get(v, v) if v else "없음")}</span></span>' for t, v in im["results"].items())
                      + "</td></tr>" for im in r.get("impacts", []))
    im_html = (f'<section id="impacts"><h2>영향 받는 기존 기능 <span class="muted small">리그레이션</span></h2>'
               f'<div class="tbl"><table><thead><tr><th>영역</th><th>기능</th><th>리그레이션 TC 결과</th></tr></thead><tbody>{im_rows}</tbody></table></div></section>') if im_rows else ""

    rv = r.get("review", {})
    f_rows = "".join(f'<tr><td class="mono">{e(f["id"])}</td><td>{e({"high": "상", "mid": "중", "low": "하"}.get(f["severity"], f["severity"]))}</td>'
                     f'<td>{e(f["category"])}</td><td>{e(f["action"])}</td><td>{e(owners.get(f.get("owner"), f.get("owner", "")))}</td></tr>' for f in rv.get("findings", []))
    risk = "".join(f"<li>{e(x)}</li>" for x in rv.get("accepted_risks", []))
    rv_html = (f'<section id="review"><h2>검토 지적 <span class="muted small">{len(rv.get("findings", []))}건</span></h2>'
               f'<p class="muted">{e(rv.get("headline", ""))}</p>' + (f'<ul class="ev">{risk}</ul>' if risk else "")
               + (f'<div class="tbl"><table><thead><tr><th>번호</th><th>정도</th><th>분류</th><th>보완 요청</th><th>담당</th></tr></thead><tbody>{f_rows}</tbody></table></div>' if f_rows else "")
               + "</section>") if rv else ""

    el_rows = "".join(f'<tr><td class="mono">{e(x["id"])}</td><td>{e(x["name"])}</td><td><span class="flag {dict(met="green", conditional="amber", unmet="red").get(x["status"], "muted")}">'
                      f'{e(dict(met="충족", conditional="조건부", unmet="미충족").get(x["status"], "참고"))}</span></td><td>{e(x["summary"])}</td></tr>' for x in r.get("elements", []))
    el_html = (f'<section id="elements"><h2>판정 근거 <span class="muted small">판정관 요소 E1~E8</span></h2>'
               f'<div class="tbl"><table><thead><tr><th>요소</th><th>이름</th><th>상태</th><th>요약</th></tr></thead><tbody>{el_rows}</tbody></table></div></section>') if el_rows else ""

    links = r.get("links", []) + [{"label": "컨플루언스 페이지" if p["where"] == "confluence" else p["where"], "url": p["url"]} for p in r.get("published", [])]
    out_rows = "".join(f'<tr><td>{e(o["step"])}</td><td><b>{e(o["name"])}</b><div class="muted small">{e(o["what"])}</div></td>'
                       + (f'<td><a href="{e(o["url"])}">열기 ↗</a></td>' if o.get("url") else f'<td class="muted small">링크 없음<div class="mono">{e(o.get("file", ""))}</div></td>')
                       + "</tr>" for o in r.get("outputs", []))
    link_li = "".join(f'<li><a href="{e(l["url"])}">{e(l["label"])}</a></li>' for l in links)
    src = "".join(f'<li>{e(x["title"])} <span class="muted small">{e(x.get("date", ""))}</span></li>' for x in r.get("sources", []))
    files_html = ('<section id="files"><h2>근거 자료</h2>'
                  + (f'<h3>QA 산출물 <span class="muted small">이 리포트가 모은 단계별 결과 — 자세한 내용은 각 문서에서</span></h3><div class="tbl"><table><thead><tr><th>만든 역할</th><th>산출물</th><th>보기</th></tr></thead><tbody>{out_rows}</tbody></table></div>' if out_rows else "")
                  + '<div class="grid">'
                  + (f'<div class="card"><h3>기획 문서</h3><ul class="ev">{src}</ul></div>' if src else "")
                  + (f'<div class="card"><h3>그 밖의 링크</h3><ul class="ev">{link_li}</ul></div>' if link_li else "")
                  + "</div></section>")

    about = r.get("about", {})
    about_html = (f'<p class="muted">{e(about.get("purpose", ""))}' + (f' 대상: {e(about["target"])}' if about.get("target") else "") + "</p>") if about.get("purpose") else ""
    css = (HERE / "templates" / "report.css").read_text(encoding="utf-8")
    kind_cls = "ok" if final else "pending"
    toc = ('<nav class="toc"><a href="#summary">요약</a>' + ('<a href="#todo">남은 일</a>' if todo_rows else "") + ('<a href="#questions">질문</a>' if q_open else "")
           + '<span class="sep">|</span>' + ('<a href="#bugs">결함</a>' if bug_rows else "") + '<a href="#run">실행 결과</a>' + ('<a href="#coverage">요구사항</a>' if cov else "")
           + ('<a href="#impacts">기존 기능</a>' if im_rows else "") + ('<a href="#review">검토</a>' if rv else "") + '<a href="#files">근거 자료</a></nav>')
    doc = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title><style>{css}</style></head>
<body><div class="wrap">
<header class="top"><span class="eyebrow">QA 오케스트라 · 신규 기능 QA 리포터</span><h1>{e(title)}</h1>
<div class="meta"><span class="kind {kind_cls}">{e(kind)}</span><span>{e(r["generated_at"][:16].replace("T", " "))}</span>{f'<span>{e(about["sprint"])}</span>' if about.get("sprint") else ""}</div>
{about_html}
{f'<div class="banner warn"><b>{e(stale)}</b></div>' if stale else ""}
{toc}</header>
<div class="part"><span>요약 · 매니저·기획용</span></div>
<section id="summary"><div class="head2">{head}{state}</div>{kpi_html}{kpi_sub}</section>
{todo_html}
{q_html}
{scope_html}
<div class="part"><span>상세 · 개발·QA용</span></div>
{bug_html}
{run_html}
{cov_html}
{im_html}
{rv_html}
{el_html}
{files_html}
<p class="muted small">리포터는 앞 단계 결과를 모아 전할 뿐 결과를 바꾸지 않는다. 편입 여부는 QA 매니저가 정한다.</p>
</div></body></html>
"""
    out = Path(outdir or Path(path).parent)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.html").write_text(doc, encoding="utf-8")
    (out / "report.md").write_text(to_md(r, cfg, title, kind, open_bugs, sev, accepted, links), encoding="utf-8")
    (out / "report.confluence.html").write_text(to_confluence(r, cfg, kind, open_bugs, sev, accepted, links), encoding="utf-8")
    if stale:
        print("주의: " + stale)
    print(f"리포트: {kind} → {out / 'report.html'}, report.md, report.confluence.html")


def decision_line(r, vl):
    dec, rec = r.get("decision"), r["recommendation"]
    if dec:
        return (f"{vl[dec['decision']]} — {dec['by']}, {dec['at'][:16].replace('T', ' ')}" + (f" — {dec['note']}" if dec.get("note") else "")
                + (f" (받아들인 조건 {', '.join(dec['conditions_accepted'])})" if dec.get("conditions_accepted") else "")
                + (f" · 판정관 추천은 {vl[rec['verdict']]}" if dec["decision"] != rec["verdict"] else ""))
    return f"결정 대기 — 판정관 추천 {vl[rec['verdict']]}: {rec['headline']}"


def to_md(r, cfg, title, kind, open_bugs, sev, accepted, links):
    rc = cfg["report"]
    vl, rl, owners = rc["verdicts"], rc["results"], rc["owners"]
    ex, res, bmap = r["execution"], r["execution"]["results"], blocker_by_id(r)
    md = [f"# {title}", "", f"_{kind} · {r['generated_at'][:16].replace('T', ' ')}_", "", "## 요약", "",
          f"**{r['summary']['headline']}**", ""]
    kb = key_bugs(r, cfg)
    if kb:
        md += [f"- **{b['severity']}** [{b['key']}]({b['url']}) {b['summary']} ({b['status'] or '상태 미조회'})" if b.get("url")
               else f"- **{b['severity']}** {b.get('key') or b['id']} {b['summary']}" for b in kb] + [""]
    md += [f"- {p}" for p in r["summary"]["points"]]
    sheet = (r.get("tc_sheet") or {}).get("url", "")
    md += ["", f"**QA 매니저 결정:** {decision_line(r, vl)}", "",
           (f"[TC {ex['total']}개]({sheet})" if sheet else f"TC {ex['total']}개") + f" · 실행 {res['pass'] + res['fail']} · " + " · ".join(f"{rl[k]} {res[k]}" for k in ORDER if res[k])
           + f" · 열린 버그 {len(open_bugs)}" + (f" ({sev})" if sev else "") + (f" — [지라에서 보기]({jira_search(open_bugs)})" if jira_search(open_bugs) else "")]
    if r.get("conditions"):
        md += ["", "## 남은 일", "", "| 번호 | 할 일 | 담당 | 해결하는 이슈 |", "|---|---|---|---|"]
        for c in r["conditions"]:
            lv = [bmap[b]["level"] for b in c.get("resolves", []) if b in bmap]
            md.append(f"| {c['id']} | {c['what']}" + (" **(받아들인 조건)**" if c["id"] in accepted else "") + f" | {owners.get(c['owner'], c['owner'])} | "
                      + ("릴리즈 불가 " if "block" in lv else "조건부 릴리즈 " if lv else "") + ", ".join(c.get("resolves", [])) + " |")
    if r.get("questions"):
        md += ["", "## 답이 필요한 질문", ""] + [f"- **{q['id']}** {q['question']} — {q.get('ask_to', '')} (걸린 TC {len(q.get('tcs_depending') or [])})" for q in r["questions"]]
    md += ["", "---", "", "## 결함", "", "| 키 | 심각도 | 상태 | TC | 내용 |", "|---|---|---|---|---|"]
    md += [f"| {b.get('key') or b.get('id')} | {b['severity']} | {b['status'] or '상태 미조회'} | {b['tc_id']} | {b['summary']} |" for b in r["bugs"]]
    md += ["", "## 실행 결과", "", "| 우선순위 | " + " | ".join(rl[k] for k in ORDER) + " |", "|---" * (len(ORDER) + 1) + "|"]
    md += [f"| {p} | " + " | ".join(str(c.get(k, 0)) for k in ORDER) + " |" for p, c in ex["by_priority"].items()]
    if r["not_passed"]:
        md += ["", "**통과하지 못한 TC**", ""] + [f"- {t['id']} {t['priority']} {rl[t['result']]} — {t['title']}" + (f" ({t['issue']})" if t.get("issue") else "") for t in r["not_passed"]]
    if r.get("coverage"):
        md += ["", "## 요구사항", "", "| 번호 | 요구사항 | 상태 |", "|---|---|---|"]
        md += [f"| {c['req_id']} | {c['keyword']} | {REQ_LABEL.get(c['status'], (c['status'],))[0]} |" for c in r["coverage"]]
    if r.get("impacts"):
        md += ["", "## 영향 받는 기존 기능", ""] + [f"- {im['name']}: " + ", ".join(f"{t} {rl.get(v, v) if v else '없음'}" for t, v in im["results"].items()) for im in r["impacts"]]
    if r.get("outputs"):
        md += ["", "## QA 산출물", ""] + [f"- **{o['name']}** ({o['step']}) — {o['what']} — " + (f"[열기]({o['url']})" if o.get("url") else f"파일 `{o.get('file', '')}`") for o in r["outputs"]]
    if links:
        md += ["", "## 그 밖의 링크", ""] + [f"- [{l['label']}]({l['url']})" for l in links]
    return "\n".join(md) + "\n"


def to_confluence(r, cfg, kind, open_bugs, sev, accepted, links):
    """컨플루언스 페이지 본문(Atlassian HTML 형식 조각). 패널·상태 표시·지라 스마트 링크를 쓴다."""
    rc = cfg["report"]
    vl, rl, owners = rc["verdicts"], rc["results"], rc["owners"]
    ex, res, bmap = r["execution"], r["execution"]["results"], blocker_by_id(r)
    dec, rec = r.get("decision"), r["recommendation"]
    color = {"include": "green", "conditional": "yellow", "hold": "red"}
    lozenge = lambda text, c: f'<span data-type="status" data-color="{c}">{e(text)}</span>'
    key = lambda b: f'<a href="{e(b["url"])}" data-card-appearance="inline"></a>' if b.get("url") else e(b.get("key") or b.get("id"))
    v = dec["decision"] if dec else rec["verdict"]
    panel = "success" if v == "include" else "warning" if v == "conditional" else "error"
    out = [f'<div data-type="panel-{panel if dec else "info"}"><p><strong>{e(kind)}</strong> · {lozenge(vl[v] if dec else "결정 대기", color[v] if dec else "neutral")}</p>'
           f'<p>{e(r["summary"]["headline"])}</p></div>',
           "".join(f'<p>{lozenge(b["severity"], "red" if b["severity"] in rc["release_blocking_severities"] else "yellow")} {key(b)} {e(b["summary"])} · {e(b["status"] or "상태 미조회")}</p>'
                   for b in key_bugs(r, cfg)),
           "<h2>요약</h2><ul>" + "".join(f"<li>{e(p)}</li>" for p in r["summary"]["points"]) + "</ul>",
           f'<p><strong>QA 매니저 결정:</strong> {e(decision_line(r, vl))}</p>',
           (f'<p><a href="{e(r["tc_sheet"]["url"])}">TC {ex["total"]}개</a>' if (r.get("tc_sheet") or {}).get("url") else f'<p>TC {ex["total"]}개') + f' · 실행 {res["pass"] + res["fail"]} · ' + " · ".join(f"{rl[k]} {res[k]}" for k in ORDER if res[k])
           + f' · 열린 버그 {len(open_bugs)}' + (f" ({e(sev)})" if sev else "")
           + (f' — <a href="{e(jira_search(open_bugs))}">지라에서 보기</a>' if jira_search(open_bugs) else "") + "</p>"]
    if r.get("conditions"):
        rows = ""
        for c in r["conditions"]:
            lv = [bmap[b]["level"] for b in c.get("resolves", []) if b in bmap]
            tag = lozenge("릴리즈 불가", "red") if "block" in lv else lozenge("조건부 릴리즈", "yellow") if lv else ""
            acc = " " + lozenge("받아들인 조건", "green") if c["id"] in accepted else ""
            rows += (f'<tr><td>{e(c["id"])}</td><td><p>{e(c["what"])}{acc}</p><p>확인: {e(c["verify"])}</p></td>'
                     f'<td>{e(owners.get(c["owner"], c["owner"]))}</td><td><p>{tag} {e(", ".join(c.get("resolves", [])))}</p></td></tr>')
        out.append(f"<h2>남은 일</h2><table><thead><tr><th>번호</th><th>할 일</th><th>담당</th><th>해결하는 이슈</th></tr></thead><tbody>{rows}</tbody></table>")
    if r.get("questions"):
        out.append("<h2>답이 필요한 질문</h2><ul>" + "".join(f'<li><strong>{e(q["id"])}</strong> {e(q["question"])} — {e(q.get("ask_to", ""))} (걸린 TC {len(q.get("tcs_depending") or [])})</li>' for q in r["questions"]) + "</ul>")
    out.append("<hr><h2>결함</h2><table><thead><tr><th>키</th><th>심각도</th><th>상태</th><th>TC</th><th>내용</th></tr></thead><tbody>"
               + "".join(f'<tr><td>{key(b)}</td><td>{lozenge(b["severity"], "red" if b["severity"] in rc["release_blocking_severities"] else "yellow")}</td><td>{e(b["status"] or "상태 미조회")}</td>'
                         f'<td>{e(b["tc_id"])}</td><td>{e(b["summary"])}</td></tr>' for b in r["bugs"]) + "</tbody></table>")
    out.append("<h2>실행 결과</h2><table><thead><tr><th>우선순위</th>" + "".join(f"<th>{e(rl[k])}</th>" for k in ORDER) + "</tr></thead><tbody>"
               + "".join(f"<tr><td>{e(p)}</td>" + "".join(f"<td>{c.get(k, 0)}</td>" for k in ORDER) + "</tr>" for p, c in ex["by_priority"].items()) + "</tbody></table>")
    if r["not_passed"]:
        out.append(f'<details><summary>통과하지 못한 TC {len(r["not_passed"])}개</summary><table><thead><tr><th>TC</th><th>우선</th><th>결과</th><th>내용</th></tr></thead><tbody>'
                   + "".join(f'<tr><td>{e(t["id"])}</td><td>{e(t["priority"])}</td><td>{e(rl[t["result"]])}</td><td><p>{e(t["title"])}</p>'
                             + (f'<p>{e(t.get("issue") or t.get("mode_reason"))}</p>' if t.get("issue") or t.get("mode_reason") else "") + "</td></tr>" for t in r["not_passed"])
                   + "</tbody></table></details>")
    if r.get("coverage"):
        cl = {"green": "green", "red": "red", "amber": "yellow", "muted": "neutral"}
        out.append(f'<details><summary>요구사항 {len(r["coverage"])}개</summary><table><thead><tr><th>번호</th><th>요구사항</th><th>상태</th></tr></thead><tbody>'
                   + "".join(f'<tr><td>{e(c["req_id"])}</td><td>{e(c["keyword"])}</td><td>{lozenge(REQ_LABEL.get(c["status"], (c["status"], "muted"))[0], cl[REQ_LABEL.get(c["status"], ("", "muted"))[1]])}</td></tr>' for c in r["coverage"])
                   + "</tbody></table></details>")
    if r.get("impacts"):
        out.append("<h2>영향 받는 기존 기능</h2><ul>" + "".join(f'<li>{e(im["name"])}: ' + e(", ".join(f"{t} {rl.get(v, v) if v else '없음'}" for t, v in im["results"].items())) + "</li>" for im in r["impacts"]) + "</ul>")
    if r.get("outputs"):
        out.append("<h2>QA 산출물</h2><table><thead><tr><th>만든 역할</th><th>산출물</th><th>보기</th></tr></thead><tbody>"
                   + "".join(f'<tr><td>{e(o["step"])}</td><td><p><strong>{e(o["name"])}</strong></p><p>{e(o["what"])}</p></td><td>'
                             + (f'<a href="{e(o["url"])}">열기</a>' if o.get("url") else f'링크 없음 · <code>{e(o.get("file", ""))}</code>') + "</td></tr>" for o in r["outputs"])
                   + "</tbody></table>")
    if links:
        out.append("<h2>그 밖의 링크</h2><ul>" + "".join(f'<li><a href="{e(l["url"])}">{e(l["label"])}</a></li>' for l in links) + "</ul>")
    out.append(f'<p style="font-size: 14px">QA 오케스트라 신규 기능 QA 리포터 · {e(r["generated_at"][:16].replace("T", " "))}</p>')
    return "\n".join(out) + "\n"


def record(args, cfg):
    r = read_json(args.report)
    if not args.url.startswith(("https://", "http://")):
        sys.exit("url은 http(s) 주소")
    pubs = [p for p in r.get("published", []) if p.get("where") != args.where]
    pubs.append({k: v for k, v in {"where": args.where, "url": args.url, "page_id": args.page_id, "at": now(), "status": r.get("status")}.items() if v})
    r["published"] = pubs
    Path(args.report).write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"올린 위치 기록: {args.where} {args.url}")
    render(args.report, None, cfg)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--judgment", required=True)
    c.add_argument("--review", required=True)
    c.add_argument("--analysis")
    c.add_argument("--tc")
    c.add_argument("--bug-status", nargs="*", default=[])
    c.add_argument("--tc-sheet-url", dest="tc_sheet_url")
    c.add_argument("--sheet-file", dest="sheet_file", help="결과가 채워진 TC 시트 파일(링크가 없을 때 보이는 위치)")
    c.add_argument("--link", nargs="*", default=[], help="산출물 링크: analysis|tc|bugs|review|judgment=<URL>")
    c.add_argument("--out", required=True)
    c.add_argument("--config")
    r = sub.add_parser("render")
    r.add_argument("report")
    r.add_argument("outdir", nargs="?")
    r.add_argument("--config")
    p = sub.add_parser("record")
    p.add_argument("report")
    p.add_argument("--where", default="confluence")
    p.add_argument("--url", required=True)
    p.add_argument("--page-id", dest="page_id")
    p.add_argument("--config")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.cmd == "collect":
        args.bug_status = dict(x.split("=", 1) for x in args.bug_status if "=" in x)
        args.link = dict(x.split("=", 1) for x in args.link if "=" in x)
        collect(args, cfg)
    elif args.cmd == "render":
        render(args.report, args.outdir, cfg)
    else:
        record(args, cfg)


if __name__ == "__main__":
    main()
