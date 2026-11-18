#!/usr/bin/env python3
"""신규 기능 판정관 도구: 검토자의 실행 결과 검토(review.json)로 요소별 판정 재료를 모으고(collect),
판정서를 그리고(render), 사람의 최종 결정을 기록한다(decide).

사용법:
  python3 judge.py collect --review <실행 결과 review.json> [--tc <test-cases.json>]
                           [--bug-status QAO-8=해야 할 일 ...] --out <폴더>
      → <폴더>/judgment-facts.json  (요소 8가지의 상태 후보·근거, 추천 후보 hint)
  python3 judge.py render <judgment.json> [출력 폴더]
      → judgment.html, judgment.md  (judgment.json = judgment-facts.json + 판정관이 쓴 recommendation·conditions)
  python3 judge.py decide <judgment.json> --decision include|conditional|hold --by "<결정한 사람>"
                          [--note "<이유>"] [--accept K-01,K-02] [--ledger <릴리즈 기록 json>]
      → judgment.json에 결정을 더하고 다시 그린다. --ledger를 주면 그 파일에 이 기능의 결정을 한 줄 남긴다.
      판정서 화면(링크)에서 제출된 결정을 옮길 때는 --source <제출 문서 id> --submitted-at <제출 시각>을 붙인다.

공통 옵션: --config <qa-orchestra.config.json>
"""
import argparse
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
VERDICTS = ["include", "conditional", "hold"]
STATUS = {"met": ("충족", "green"), "conditional": ("조건부", "amber"), "unmet": ("미충족", "red"), "info": ("참고", "muted")}
RANK = {"met": 0, "info": 0, "conditional": 1, "unmet": 2}


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


def element(eid, name, status, summary, evidence, refs=()):
    return {"id": eid, "name": name, "status": status, "summary": summary, "evidence": list(evidence), "refs": list(refs)}


def collect(args, cfg):
    jc = cfg["judge"]
    rv = read_json(args.review)
    if rv.get("stage") != "result":
        sys.exit("판정에는 검토자의 '실행 결과 검토'(stage result) review.json이 필요함 — 실행 전 검토로는 판정하지 않는다")
    top = jc["top_priority"]
    tcs = {c["id"]: c for c in (read_json(args.tc)["cases"] if args.tc else [])}
    rows = rv.get("tc_results", [])
    by_id = {r["id"]: r for r in rows}
    count = lambda k: sum(1 for r in rows if r["result"] == k)
    els = []

    # E1 실행 완료
    unrun = [r for r in rows if r["result"] in ("blank", "error")]
    unrun_top = [r["id"] for r in unrun if r["priority"] == top]
    status = "unmet" if unrun_top else "conditional" if unrun else "met"
    els.append(element("E1", "실행 완료", status,
                       f"{len(rows)}개 중 실행 {count('pass') + count('fail')} · 판정 보류 {count('hold')} · 미실행 {len(unrun)}"
                       + (f"({top} {len(unrun_top)}개)" if unrun_top else ""),
                       [f"{r['id']} {r['priority']} {r['title']} — {r['mode'] or '미실행'}{': ' + r['mode_reason'] if r.get('mode_reason') else ''}" for r in unrun],
                       [r["id"] for r in unrun]))

    # E2 결함
    seen, bugs = set(), []
    for b in rv.get("bugs", []):
        if b.get("key") and b["key"] not in seen:
            seen.add(b["key"])
            bugs.append(dict(b, status=args.bug_status.get(b["key"], "")))
    closed = {s.lower() for s in jc["closed_statuses"]}
    open_bugs = [b for b in bugs if (b["status"] or "").lower() not in closed]
    blocking = [b for b in open_bugs if b.get("severity") in jc["blocking_severities"]]
    cond = [b for b in open_bugs if b.get("severity") in jc["conditional_severities"]]
    unknown = [b for b in open_bugs if not b["status"]]
    fail_no_bug = [r["id"] for r in rows if r["result"] == "fail" and not any(by_id[r["id"]]["bugs"])]
    status = "unmet" if blocking else "conditional" if (cond or open_bugs or fail_no_bug) else "met"
    ev = [f"{b['key']} {b.get('severity', '')} · {b['status'] or '상태 확인 필요'} · {b.get('tc_id', '')} {b.get('summary', '')}" for b in open_bugs]
    ev += [f"{t}: Fail인데 등록된 버그 없음" for t in fail_no_bug]
    els.append(element("E2", "결함", status,
                       f"열린 버그 {len(open_bugs)}건(" + " · ".join(f"{s} {sum(1 for b in open_bugs if b.get('severity') == s)}"
                                                         for s in dict.fromkeys(b.get("severity") for b in open_bugs)) + ")"
                       + (f", 상태 모름 {len(unknown)}" if unknown else "") if open_bugs else "열린 버그 없음",
                       ev, [b["key"] for b in open_bugs] + fail_no_bug))

    # E3 판정 보류
    holds = [r for r in rows if r["result"] == "hold"]
    hold_top = [r["id"] for r in holds if r["priority"] == top]
    status = "unmet" if hold_top else "conditional" if holds else "met"
    qs = sorted({q for r in holds for q in re.findall(r"\bQ\d+\b", r["issue"])}, key=lambda x: int(x[1:]))
    els.append(element("E3", "판정 보류", status,
                       f"{len(holds)}개" + (f"({top} {len(hold_top)}개)" if hold_top else "") + (f" — 필요한 답 {', '.join(qs)}" if qs else ""),
                       [f"{r['id']} {r['priority']} — {r['issue']}" for r in holds], [r["id"] for r in holds] + qs))

    # E4 가정에 기댄 판정 (Fail은 TC의 assumptions, Pass는 검토자 PASS_ON_ASSUMPTION)
    fail_assumed = [(r["id"], tcs[r["id"]].get("assumptions") or []) for r in rows
                    if r["result"] == "fail" and tcs.get(r["id"], {}).get("assumptions")]
    pass_assumed = {}
    for c in rv.get("checks", []):
        if c["code"] == "PASS_ON_ASSUMPTION":
            pass_assumed[c["targets"][0]] = c["targets"][1:]
    n_pass = len({t for v in pass_assumed.values() for t in v})
    status = "conditional" if (fail_assumed or pass_assumed) else "met"
    ev = [f"{t} Fail — 가정 {', '.join(a)}: 답이 가정과 다르면 결함이 아닐 수 있음" for t, a in fail_assumed]
    ev += [f"{q}: Pass {len(v)}개({', '.join(v)})" for q, v in pass_assumed.items()]
    els.append(element("E4", "가정에 기댄 판정", status,
                       f"Fail {len(fail_assumed)}개 · Pass {n_pass}개가 답이 안 온 질문의 임시 가정 기준" if status != "met" else "가정에 기댄 판정 없음",
                       ev, [t for t, _ in fail_assumed] + list(pass_assumed)))
    if not args.tc:
        els[-1]["evidence"].append("--tc를 주지 않아 Fail 쪽 가정은 확인하지 못함")

    # E5 요구사항 충족
    cov = rv.get("coverage", [])
    cleared = {t for d in rv.get("dismissed", []) if d.get("code") == "REQ_NO_BOUNDARY" for t in d.get("targets", [])}
    st = {}
    for c in cov:
        k = "ok" if c["status"] == "weak" and c["req_id"] in cleared else c["status"]
        st.setdefault(k, []).append(c)
    status = "unmet" if st.get("missing") else "conditional" if (st.get("failing") or st.get("unverified") or st.get("weak")) else "met"
    label = {"ok": "충족", "failing": "Fail 있음", "unverified": "확인 덜 됨", "missing": "TC 없음", "weak": "커버 약함", "excluded": "제외"}
    els.append(element("E5", "요구사항 충족", status,
                       f"{len(cov)}개 중 " + " · ".join(f"{label.get(k, k)} {len(v)}" for k, v in st.items()),
                       [f"{c['req_id']} {c['keyword']}: {label.get(c['status'], c['status'])} {c.get('results', {})}" for k in ("missing", "failing", "unverified", "weak")
                        for c in st.get(k, [])], [c["req_id"] for k in ("missing", "failing", "unverified") for c in st.get(k, [])]))

    # E6 고위험 영역
    ev, worst = [], "met"
    for r in rv.get("risks", []):
        if r.get("level") != "high":
            continue
        res = [by_id[t]["result"] for t in r.get("tcs", []) if t in by_id]
        s = "unmet" if "fail" in res else "conditional" if (not res or any(x != "pass" for x in res)) else "met"
        worst = max(worst, s, key=RANK.get)
        ev.append(f"{r['area']}: TC {len(res)}개 — " + (" · ".join(f"{k} {res.count(k)}" for k in dict.fromkeys(res)) if res else "연결된 TC 없음")
                  + f" → {STATUS[s][0]}")
    els.append(element("E6", "고위험 영역", worst, f"고위험 {len(ev)}곳", ev))

    # E7 검토 지적
    summary = rv.get("summary", {})
    findings = rv.get("findings", [])
    highs = [f for f in findings if f.get("severity") == "high"]
    status = "conditional" if (summary.get("status") == "needs_work" or highs) else "met"
    ev = [f"검토 상태: {'보완 후 판정' if summary.get('status') == 'needs_work' else '판정관에게 넘김'} — {summary.get('headline', '')}"]
    ev += [f"{f['id']} [{f['severity']}] {f['category']}: {f['action']}" for f in highs]
    ev += [n for n in summary.get("notes", []) if n.startswith("감수한 위험")]
    els.append(element("E7", "검토 지적", status, f"지적 {len(findings)}건(상 {len(highs)})", ev, [f["id"] for f in highs]))

    # E8 영향 범위 (리그레이션 TC 결과)
    ev, worst = [], "met"
    for im in rv.get("impacts", []):
        res = [by_id[t]["result"] for t in im.get("tcs", []) if t in by_id]
        s = "unmet" if "fail" in res else "conditional" if (not res or any(x != "pass" for x in res)) else "met"
        worst = max(worst, s, key=RANK.get)
        ev.append(f"{im['name']}: " + (" · ".join(f"{t} {by_id[t]['result']}" for t in im["tcs"] if t in by_id) or "리그레이션 TC 없음") + f" → {STATUS[s][0]}")
    els.append(element("E8", "영향 범위(기존 기능)", worst, f"직접 영향 기능 {len(ev)}개", ev))

    worst = max((e["status"] for e in els), key=RANK.get)
    hint = {"unmet": "hold", "conditional": "conditional", "met": "include"}[worst]

    # 막는 이유 후보: 요소 근거를 '하나씩 풀 수 있는 단위'로 쪼갠다. block = 남아 있으면 편입 보류, cond = 조건부
    blockers = []
    add = lambda level, title, el, refs: blockers.append({"id": f"B-{len(blockers) + 1:02d}", "level": level, "title": title,
                                                          "element": el, "refs": list(refs)})
    for b in blocking:
        add("block", f"{b.get('severity')} 결함 {b['key']} 미해결 — {b.get('summary', '')}", "E2", [b["key"], b.get("tc_id") or ""])
    for b in cond:
        add("cond", f"{b.get('severity')} 결함 {b['key']} 미해결 — {b.get('summary', '')}", "E2", [b["key"], b.get("tc_id") or ""])
    if unrun_top:
        add("block", f"{top} TC 미실행: {', '.join(unrun_top)}", "E1", unrun_top)
    rest = [r["id"] for r in unrun if r["id"] not in unrun_top]
    if rest:
        add("cond", f"그 밖의 미실행 TC {len(rest)}개", "E1", rest)
    if hold_top:
        add("block", f"{top} TC 판정 보류: {', '.join(hold_top)}", "E3", hold_top)
    rest = [r["id"] for r in holds if r["id"] not in hold_top]
    if rest:
        add("cond", f"그 밖의 판정 보류 TC {len(rest)}개", "E3", rest)
    if fail_assumed:
        qs4 = sorted({q for _, a in fail_assumed for q in a}, key=lambda x: int(x[1:]))
        add("cond", f"Fail {len(fail_assumed)}건의 결함 여부가 {', '.join(qs4)} 답에 달림", "E4", [t for t, _ in fail_assumed] + qs4)
    for line in els[5]["evidence"]:
        if line.endswith("미충족"):
            add("block", f"고위험 영역 Fail — {line.split(':')[0]}", "E6", [])
    for im in rv.get("impacts", []):
        res = [by_id[t]["result"] for t in im.get("tcs", []) if t in by_id]
        if "fail" in res:
            add("block", f"기존 기능 리그레이션 Fail — {im['name']}", "E8", im["tcs"])
    facts = {
        "feature": rv.get("feature", ""), "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "inputs": {k: v for k, v in {"review": args.review, "tc": args.tc}.items() if v},
        "bug_status_checked": bool(args.bug_status), "elements": els, "hint": hint, "blockers": blockers,
        "impacts": rv.get("impacts", []), "questions": [q for q in rv.get("questions", []) if q.get("hold_tcs") or q.get("tcs_depending")],
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "judgment-facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"막는 이유 후보: 막음 {sum(b['level'] == 'block' for b in blockers)} · 조건부 {sum(b['level'] == 'cond' for b in blockers)}")
    print(f"judgment-facts.json: 추천 후보 {jc['verdicts'][hint]} — " + " · ".join(f"{e['id']} {e['name']} {STATUS[e['status']][0]}" for e in els))


# ── render ──
e = lambda s: html.escape(str(s if s is not None else ""))


def validate(j, cfg):
    errs = []
    owners = cfg["judge"]["owners"]
    rec = j.get("recommendation") or {}
    if rec.get("verdict") not in VERDICTS:
        errs.append(f"recommendation.verdict는 {'/'.join(VERDICTS)}")
    if not rec.get("headline"):
        errs.append("recommendation.headline(한 문장)이 없음")
    if not rec.get("reasons"):
        errs.append("recommendation.reasons(근거 요소 번호를 단 이유 목록)가 없음")
    if rec.get("verdict") and rec.get("verdict") != j.get("hint") and not rec.get("deviation_reason"):
        errs.append(f"추천({rec.get('verdict')})이 기준표 후보({j.get('hint')})와 다름 — recommendation.deviation_reason 필요")
    for el in j.get("elements", []):
        if el.get("status") not in STATUS:
            errs.append(f"{el.get('id')}.status는 {'/'.join(STATUS)}")
    conds = j.get("conditions", [])
    if rec.get("verdict") in ("conditional", "hold") and not conds:
        errs.append("조건부·보류면 conditions(편입하려면 풀어야 할 조건)가 필요")
    ids = set()
    for i, c in enumerate(conds):
        for k in ("id", "what", "owner", "verify"):
            if not c.get(k):
                errs.append(f"conditions[{i}].{k} 없음")
        if c.get("owner") and c["owner"] not in owners:
            errs.append(f"conditions[{i}].owner는 {', '.join(owners)} 중 하나")
        if c.get("id") in ids:
            errs.append(f"conditions[{i}].id 중복")
        ids.add(c.get("id"))
    blockers = j.get("blockers", [])
    bids = {b.get("id") for b in blockers}
    for i, b in enumerate(blockers):
        if b.get("level") not in ("block", "cond") or not b.get("title"):
            errs.append(f"blockers[{i}]: level(block/cond)·title 필요")
    for i, c in enumerate(conds):
        bad = [x for x in c.get("resolves", []) if x not in bids]
        if not c.get("resolves"):
            errs.append(f"conditions[{i}] {c.get('id')}: resolves(이 할 일이 푸는 막는 이유 B-번호)가 없음")
        elif bad:
            errs.append(f"conditions[{i}] {c.get('id')}: 없는 막는 이유 {', '.join(bad)}")
    solved = {x for c in conds for x in c.get("resolves", [])}
    errs += [f"막는 이유 {b['id']}를 푸는 할 일(conditions)이 없음" for b in blockers if b.get("id") not in solved]
    if rec.get("verdict") == "hold" and not any(b.get("level") == "block" for b in blockers):
        errs.append("편입 보류인데 '막음'(block) 이유가 없음 — 무엇이 막는지 blockers에 적는다")
    if rec.get("verdict") == "include" and blockers:
        errs.append("편입 가능인데 막는 이유가 남아 있음 — 풀렸으면 blockers에서 뺀다")
    for i, d in enumerate(j.get("decisions", [])):
        if d.get("decision") not in VERDICTS or not d.get("by"):
            errs.append(f"decisions[{i}]: decision·by가 필요")
        if d.get("decision") != rec.get("verdict") and not d.get("note"):
            errs.append(f"decisions[{i}]: 추천과 다른 결정에는 note(이유)가 필요")
        bad = [x for x in d.get("conditions_accepted", []) if x not in ids]
        if bad:
            errs.append(f"decisions[{i}]: 없는 조건 번호 {', '.join(bad)}")
    return errs


def pill(status):
    label, cls = STATUS[status]
    return f'<span class="flag {cls}">{label}</span>'


def render(path, outdir, cfg):
    j = read_json(path)
    errs = validate(j, cfg)
    if errs:
        sys.exit("judgment.json 점검 실패:\n  - " + "\n  - ".join(errs))
    vlabel = cfg["judge"]["verdicts"]
    owners = cfg["judge"]["owners"]
    rec = j["recommendation"]
    vcls = {"include": "ok", "conditional": "warn", "hold": "bad"}
    decisions = j.get("decisions", [])
    last = decisions[-1] if decisions else None

    if last:
        diff = " (추천과 다름)" if last["decision"] != rec["verdict"] else ""
        dec_html = (f'<div class="decision {vcls[last["decision"]]}"><span class="eyebrow">사람의 결정</span><b>{e(vlabel[last["decision"]])}{e(diff)}</b>'
                    f'<span>{e(last["by"])} · {e(last["at"][:16].replace("T", " "))}</span>'
                    + (f'<span>{e(last["note"])}</span>' if last.get("note") else "")
                    + (f'<span>받아들인 조건: {e(", ".join(last["conditions_accepted"]))}</span>' if last.get("conditions_accepted") else "")
                    + "</div>")
    else:
        dec_html = ('<div class="decision pending"><span class="eyebrow">사람의 결정</span><b>아직 결정 전</b>'
                    '<span>QA 매니저가 추천안을 보고 정한다 — 판정관에게 "편입하자 / 조건부로 가자 / 보류하자"와 이유를 말하면 기록된다</span></div>')

    reasons = "".join(f"<li>{e(r)}</li>" for r in rec["reasons"])
    dev = f'<p class="muted small">기준표 후보는 {e(vlabel[j["hint"]])}였지만 다르게 추천: {e(rec["deviation_reason"])}</p>' if rec.get("deviation_reason") else ""
    head = (f'<div class="verdict {vcls[rec["verdict"]]}"><span class="eyebrow">판정관 추천</span><b>{e(vlabel[rec["verdict"]])}</b>'
            f'<span>{e(rec["headline"])}</span><ul>{reasons}</ul>{dev}</div>')

    el_rows = []
    for el in j["elements"]:
        ev = "".join(f"<li>{e(x)}</li>" for x in el.get("evidence", []))
        note = f'<p class="note">{e(el["note"])}</p>' if el.get("note") else ""
        n_ev = len(el.get("evidence", []))
        det = f'<details><summary>근거 {n_ev}건</summary><ul class="ev">{ev}</ul></details>' if ev else ""
        el_rows.append(f'<tr><td class="mono">{e(el["id"])}</td><td><b>{e(el["name"])}</b></td><td>{pill(el["status"])}</td>'
                       f'<td>{e(el["summary"])}{note}{det}</td></tr>')
    el_html = ('<div class="tbl"><table><thead><tr><th>요소</th><th>이름</th><th>상태</th><th>요약·근거</th></tr></thead>'
               f'<tbody>{"".join(el_rows)}</tbody></table></div>')

    by_c = {c["id"]: c for c in j.get("conditions", [])}
    bl = j.get("blockers", [])
    order = sorted(bl, key=lambda b: (b["level"] != "block", b["id"]))
    cards = []
    for b in order:
        todo = [c for c in j.get("conditions", []) if b["id"] in c.get("resolves", [])]
        steps = "".join(f'<li><span class="mono kid">{e(c["id"])}</span> {e(c["what"])}<span class="who">{e(owners[c["owner"]])}</span>'
                        f'<span class="muted small">확인: {e(c["verify"])}</span></li>' for c in todo)
        refs = "".join(f"<span class=chip>{e(x)}</span>" for x in b.get("refs", []) if x)
        lvl = ("막음", "red") if b["level"] == "block" else ("조건부", "amber")
        cards.append(f'<div class="bmap {b["level"]}"><div class="why"><span class="flag {lvl[1]}">{lvl[0]}</span> <span class="mono muted small">{e(b["id"])} · {e(b.get("element", ""))}</span>'
                     f'<b>{e(b["title"])}</b><div>{refs}</div></div><div class="arrow" aria-hidden="true">→</div>'
                     f'<ol class="todo">{steps}</ol></div>')
    n_block = sum(1 for b in bl if b["level"] == "block")
    n_cond = len(bl) - n_block
    lead = (f'막는 이유 {n_block}개가 모두 풀려야 편입할 수 있어요. 조건부 {n_cond}개는 풀거나, 남긴 채 편입할지 QA 매니저가 정해요.' if n_block
            else f'막는 이유는 없어요. 조건부 {n_cond}개를 풀거나, 남긴 채 편입할지 QA 매니저가 정해요.' if n_cond else "")
    map_html = (f'<section id="map"><h2>왜 이 판정인가 → 무엇을 하면 풀리나 <span class="muted small">막음 {n_block} · 조건부 {n_cond}</span></h2>'
                f'<p class="muted">{e(lead)}</p><div class="bmaps">{"".join(cards)}</div></section>') if bl else ""
    cond_rows = "".join(
        f'<tr><td class="mono">{e(c["id"])}</td><td>{e(c["what"])}</td><td>{e(owners[c["owner"]])}</td><td>{e(c["verify"])}</td>'
        f'<td>{"".join(f"<span class=chip>{e(x)}</span>" for x in c.get("resolves", []))}</td></tr>' for c in j.get("conditions", []))
    cond_html = (f'<section id="conditions"><h2>할 일 전체 <span class="muted small">편입 조건</span></h2><div class="tbl"><table><thead><tr><th>번호</th><th>할 일</th><th>담당</th><th>확인 방법</th><th>푸는 이유</th></tr></thead>'
                 f'<tbody>{cond_rows}</tbody></table></div></section>') if cond_rows else ""

    alt = j.get("alternatives", [])
    alt_html = ""
    if alt:
        cards = "".join(f'<div class="card"><h3>{e(a["option"])}</h3><p><b class="good">장점</b> {e(a.get("pros", ""))}</p><p><b class="bad">단점</b> {e(a.get("cons", ""))}</p></div>' for a in alt)
        alt_html = f'<section id="alternatives"><h2>다른 선택지</h2><div class="grid">{cards}</div></section>'

    hist = ""
    if len(decisions) > 1:
        items = "".join(f'<li>{e(d["at"][:16].replace("T", " "))} · {e(vlabel[d["decision"]])} · {e(d["by"])}{" — " + e(d["note"]) if d.get("note") else ""}</li>' for d in decisions)
        hist = f'<section id="history"><h2>결정 이력</h2><ul class="ev">{items}</ul></section>'

    form_html = decision_form(j, cfg)
    css = (HERE / "templates" / "judgment.css").read_text(encoding="utf-8")
    title = f'{j.get("feature", "")} 신규 기능 판정'
    inputs = " · ".join(f"{k}: {Path(v).name}" for k, v in j.get("inputs", {}).items())
    doc = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title><style>{css}</style></head>
<body><div class="wrap">
<header class="top"><span class="eyebrow">QA 오케스트라 · 신규 기능 판정관</span><h1>{e(title)}</h1>
<div class="meta"><span>{e(j.get("generated_at", "")[:16].replace("T", " "))}</span><span>{e(inputs)}</span>{'' if j.get('bug_status_checked') else '<span>버그 상태 미조회</span>'}</div></header>
<div class="head2">{head}{dec_html}</div>
{map_html}
{cond_html}
<section id="elements"><h2>요소별 근거 <span class="muted small">E1~E8</span></h2>{el_html}</section>
{alt_html}
{hist}
{form_html}
<p class="muted small">판정관은 근거와 추천안만 낸다. 편입 여부는 사람이 정한다.</p>
</div></body></html>
"""
    out = Path(outdir or Path(path).parent)
    out.mkdir(parents=True, exist_ok=True)
    (out / "judgment.html").write_text(doc, encoding="utf-8")

    md = [f"# {title}", "", f"**판정관 추천: {vlabel[rec['verdict']]}** — {rec['headline']}", ""] + [f"- {r}" for r in rec["reasons"]]
    if rec.get("deviation_reason"):
        md += ["", f"_기준표 후보는 {vlabel[j['hint']]} — 다르게 추천한 이유: {rec['deviation_reason']}_"]
    md += ["", "**사람의 결정:** " + (f"{vlabel[last['decision']]} ({last['by']}, {last['at'][:16].replace('T', ' ')})" + (f" — {last['note']}" if last.get("note") else "") if last else "아직 결정 전")]
    if j.get("blockers"):
        md += ["", "## 왜 이 판정인가 → 무엇을 하면 풀리나"]
        for b in sorted(j["blockers"], key=lambda b: (b["level"] != "block", b["id"])):
            todo = [c for c in j.get("conditions", []) if b["id"] in c.get("resolves", [])]
            md += ["", f"- **[{'막음' if b['level'] == 'block' else '조건부'}] {b['id']} {b['title']}**"]
            md += [f"  - {c['id']} {c['what']} ({owners[c['owner']]}) — 확인: {c['verify']}" for c in todo]
    md += ["", "## 요소별 근거", "", "| 요소 | 이름 | 상태 | 요약 |", "|---|---|---|---|"]
    md += [f"| {el['id']} | {el['name']} | {STATUS[el['status']][0]} | {el['summary']} |" for el in j["elements"]]
    if j.get("conditions"):
        md += ["", "## 편입 조건", "", "| 번호 | 조건 | 담당 | 확인 방법 |", "|---|---|---|---|"]
        md += [f"| {c['id']} | {c['what']} | {owners[c['owner']]} | {c['verify']} |" for c in j["conditions"]]
    for a in alt:
        md += ["", f"- 선택지 **{a['option']}** — 장점: {a.get('pros', '')} / 단점: {a.get('cons', '')}"]
    (out / "judgment.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"판정서: 추천 {vlabel[rec['verdict']]} · 결정 {vlabel[last['decision']] if last else '전'} → {out / 'judgment.html'}, {out / 'judgment.md'}")


FORM_JS = (HERE / "templates" / "decision_form.js")


def decision_form(j, cfg):
    """판정서 링크(claude.ai)에서 QA 매니저가 결정을 직접 남기는 칸. 파일로 열면 안내만 보인다."""
    vlabel = cfg["judge"]["verdicts"]
    data = {"recommendation": j["recommendation"]["verdict"], "verdicts": vlabel, "generated_at": j.get("generated_at", ""),
            "conditions": [{"id": c["id"], "what": c["what"]} for c in j.get("conditions", [])],
            "recorded": [d["source"] for d in j.get("decisions", []) if d.get("source")]}
    radios = "".join(
        f'<label class="opt"><input type="radio" name="decision" id="dec-{k}" value="{k}"{" checked" if k == data["recommendation"] else ""}>'
        f'<span>{e(v)}{" (추천)" if k == data["recommendation"] else ""}</span></label>' for k, v in vlabel.items())
    conds = "".join(
        f'<label class="opt"><input type="checkbox" name="cond" id="cond-{e(c["id"])}" value="{e(c["id"])}"><span><b class="mono">{e(c["id"])}</b> {e(c["what"])}</span></label>'
        for c in data["conditions"])
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f"""<section id="decide"><h2>결정 남기기</h2>
<div class="card form-card">
<p class="muted small" id="df-status">이 칸은 claude.ai 판정서 링크에서 열었을 때만 저장돼요. 파일로 열었다면 Claude에게 결정·조건·이유를 말로 전달해 주세요.</p>
<form id="df" hidden>
<fieldset><legend>결정</legend><div class="opts">{radios}</div></fieldset>
{f'<fieldset><legend>받아들이는 편입 조건</legend><div class="opts">{conds}</div></fieldset>' if conds else ''}
<label class="field" for="df-by"><span>결정한 사람 (이름·역할)</span><input id="df-by" type="text" maxlength="80" placeholder="예: QA 매니저 김OO" required></label>
<label class="field" for="df-note"><span>이유 <em class="muted" id="df-note-hint">(추천과 다르게 결정하면 필수)</em></span><textarea id="df-note" rows="3" maxlength="1000"></textarea></label>
<div class="actions"><button type="submit" id="df-submit">결정 저장</button><span class="muted small" id="df-msg" role="status"></span></div>
</form>
<div id="df-list" hidden><h3>이 화면에서 제출된 결정</h3><ul class="ev" id="df-items"></ul>
<p class="muted small">판정관이 제출된 결정을 판정서에 옮기면 위 '사람의 결정'에 반영돼요.</p></div>
</div>
<script type="application/json" id="df-data">{payload}</script>
<script>{FORM_JS.read_text(encoding="utf-8")}</script>
</section>"""


def decide(args, cfg):
    j = read_json(args.judgment)
    d = {"decision": args.decision, "by": args.by, "at": datetime.now().astimezone().isoformat(timespec="seconds")}
    if args.note:
        d["note"] = args.note
    if args.accept:
        d["conditions_accepted"] = [x.strip() for x in args.accept.split(",") if x.strip()]
    if args.source:
        if any(x.get("source") == args.source for x in j.get("decisions", [])):
            sys.exit(f"이미 옮긴 제출입니다: {args.source}")
        d["source"] = args.source
        if args.submitted_at:
            d["submitted_at"] = args.submitted_at
    j.setdefault("decisions", []).append(d)
    errs = validate(j, cfg)
    if errs:
        sys.exit("결정 기록 실패:\n  - " + "\n  - ".join(errs))
    Path(args.judgment).write_text(json.dumps(j, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.ledger:
        led = Path(args.ledger)
        data = read_json(led) if led.exists() else {"features": []}
        data["features"] = [f for f in data["features"] if f.get("feature") != j.get("feature")]
        data["features"].append({"feature": j.get("feature"), "decision": d["decision"], "by": d["by"], "at": d["at"],
                                 "recommendation": j["recommendation"]["verdict"], "conditions": [c for c in j.get("conditions", [])
                                 if c["id"] in d.get("conditions_accepted", [])], "judgment": str(Path(args.judgment))})
        led.parent.mkdir(parents=True, exist_ok=True)
        led.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"릴리즈 기록에 남김: {led}")
    render(args.judgment, None, cfg)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--review", required=True)
    c.add_argument("--tc")
    c.add_argument("--bug-status", nargs="*", default=[])
    c.add_argument("--out", required=True)
    c.add_argument("--config")
    r = sub.add_parser("render")
    r.add_argument("judgment")
    r.add_argument("outdir", nargs="?")
    r.add_argument("--config")
    d = sub.add_parser("decide")
    d.add_argument("judgment")
    d.add_argument("--decision", choices=VERDICTS, required=True)
    d.add_argument("--by", required=True)
    d.add_argument("--note")
    d.add_argument("--accept")
    d.add_argument("--ledger")
    d.add_argument("--source", help="판정서 화면에서 제출된 결정의 문서 id (같은 제출을 두 번 옮기지 않게)")
    d.add_argument("--submitted-at", dest="submitted_at")
    d.add_argument("--config")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.cmd == "collect":
        args.bug_status = dict(x.split("=", 1) for x in args.bug_status if "=" in x)
        collect(args, cfg)
    elif args.cmd == "render":
        render(args.judgment, args.outdir, cfg)
    else:
        decide(args, cfg)


if __name__ == "__main__":
    main()
