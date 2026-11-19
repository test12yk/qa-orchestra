#!/usr/bin/env python3
"""회귀 검토자 도구: 릴리즈 회귀 결과(자동화·수동)와 운영 결함을 모으고(collect), 검토 결과를 보고서로 그린다(render).

사용법:
  python3 reg_review.py collect --results <회귀 자동화 results.json> --suite <suite.json> --selection <selection.json>
                                [--sheet <회귀 실행 결과 시트.xlsx>] [--ops <ops-defects.json>] --out <폴더>
      → <폴더>/review-facts.json  (케이스별 결과, Fail마다 원인 후보(hint)·근거, 미실행·불안정, 운영 결함별 편입 후보·관련 케이스)
  python3 reg_review.py render <review.json> [출력 폴더]
      → review.html, review.md, ops-candidates.json  (review.json = facts + 검토자가 쓴 summary·fail_reviews·ops_reviews)

ops-defects.json은 검토자가 지라에서 읽어 만든다(형식: templates/ops-defects.example.json).
공통 옵션: --config <qa-orchestra.config.json>
"""
import argparse
import difflib
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
sys.path.insert(0, str(HERE))
import xlsx  # noqa: E402

AUTO_RESULTS = {"pass", "fail"}
ATTENTION = {"flaky": "불안정", "hold": "판정 보류", "error": "자동화 오류", "stale": "테스트 고치기 필요"}
NOT_RUN_WHY = {"manual": "수동 케이스", "to_manual": "수동으로 바꿀 후보", "todo": "자동화 아직 안 됨", "stale": "테스트 고치기 필요",
               "flaky": "불안정(사람 확인)", "hold": "판정 보류", "error": "자동화 오류", None: "결과 없음"}
STATUS = {"ready": ("릴리즈 판정관에게 넘김", "green"), "needs_work": ("남은 확인 뒤 판정", "amber")}
REC = {"include": ("회귀 편입 후보", "amber"), "close": ("수정 확인 후 종료", "muted"), "defer": ("수정 뒤 다시 보기", "amber")}
CONF = {"high": "근거 충분", "mid": "근거 일부", "low": "확인 필요"}
PENDING = "확인 필요"
e = lambda s: html.escape(str(s if s is not None else ""))


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


def read_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def write_json(p, d):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def norm(s):
    return re.sub(r"\([^)]*\)|[\s·,/()\-]", "", str(s or ""))


def similar(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def matches(case, name):
    core = norm(name)
    sec = norm(case.get("section", ""))
    return bool(sec) and (sec in core or core in sec or all(t in name for t in re.findall(r"\w{2,}", case.get("section", ""))))


def day(s):
    return datetime.fromisoformat(str(s)[:10]).date() if s else None


# ── collect ──
def sheet_results(path, cfg):
    """회귀 실행 시트(여러 탭)에서 사람이 채운 결과: {CaseID: (결과, 이슈내용)}"""
    s = cfg["suite"]
    by_label = {c["label"].strip().lower(): c["key"] for c in s["columns"]}
    rv = {v: k for k, v in s["results"].items()}
    out = {}
    for _, rows in xlsx.read(path):
        if not rows:
            continue
        cols = {by_label[str(h).strip().lower()]: i for i, h in enumerate(rows[0]) if str(h).strip().lower() in by_label}
        if not all(k in cols for k in ("id", "result")):
            continue
        for r in rows[1:]:
            cell = lambda k: str(r[cols[k]]).strip() if k in cols and len(r) > cols[k] else ""
            if cell("id"):
                out[cell("id")] = (rv.get(cell("result")), cell("issue"))
    return out


def selection_view(sel):
    over = {o["id"]: o for o in sel.get("overrides", [])}
    out = {}
    for c in sel["cases"]:
        o = over.get(c["id"])
        out[c["id"]] = {"category": o["category"] if o else c["category"], "reason": o["reason"] if o else c["reason"]}
    return out


def fail_hint(case, impacts, feats):
    if case.get("last_result") == "fail":
        return "existing", "지난 회귀에서도 Fail"
    own = [f for f in feats if f and f in case.get("source", "")]
    if own:
        return "change", f"이번 릴리즈 기능({own[0]})에서 편입된 케이스"
    if impacts:
        return "change", "이번 변경 영향 영역: " + ", ".join(f"{n}({'직접' if lv == 'direct' else '간접'})" for n, lv in impacts) + " · 지난 회귀 Pass"
    return None, "이번 변경 영향 밖 — 환경·데이터, TC가 정책과 맞는지부터 확인"


def ops_facts(ops, cases, sel_view, cfg):
    o = cfg["ops"]
    closed = {x.lower() for x in o["closed_statuses"]}
    win, need = o["repeat_window_days"], o["repeat_count"]
    issues = ops.get("issues", [])
    out = []
    for i in issues:
        same = [x for x in issues if norm(x.get("area")) and norm(x.get("area")) == norm(i.get("area"))
                and day(x.get("created")) and day(i.get("created")) and abs((day(x["created"]) - day(i["created"])).days) <= win]
        sev_hit = i.get("severity") in o["include_severities"]
        rep_hit = len(same) >= need
        why = []
        if sev_hit:
            why.append(f"심각도 {i['severity']}")
        if rep_hit:
            why.append(f"반복: '{i.get('area')}' {win}일 안 {len(same)}건({', '.join(x['key'] for x in same)})")
        related = []
        for c in cases.values():
            hit_area = matches(c, i.get("area", "")) or (norm(c.get("section")) and norm(c.get("section")) == norm(i.get("area")))
            ratio = similar(c["title"], i.get("summary", ""))
            if hit_area or ratio >= 0.35:
                sv = sel_view.get(c["id"], {})
                related.append({"id": c["id"], "title": c["title"], "priority": c.get("priority", ""), "automation": c.get("automation", ""),
                                "why": "같은 영역" if hit_area else f"제목 비슷함 {ratio:.2f}",
                                "this_release": sv.get("category", "-"), "this_release_reason": sv.get("reason", "")})
        out.append(dict(i, closed=str(i.get("status", "")).lower() in closed,
                        hint="include" if (sev_hit or rep_hit) else "close",
                        hint_why=" · ".join(why) or f"{i.get('severity', '-')} · 단발", repeat_with=[x["key"] for x in same if x["key"] != i["key"]],
                        related=related))
    return out


def cmd_collect(args, cfg):
    res = read_json(args.results)
    st = read_json(args.suite)
    cases = {c["id"]: c for c in st["cases"] if not c.get("retired")}
    sel = read_json(args.selection)
    sv = selection_view(sel)
    human = sheet_results(args.sheet, cfg) if args.sheet else {}
    feats = [f.get("feature", "") for f in sel.get("features", [])]
    imps = [(i["name"], i["level"]) for i in sel.get("impacts", [])]
    rows, fails, attention, not_run = [], [], [], []
    scope = [cid for cid, v in sv.items() if v["category"] != "skip"]
    for cid in scope:
        c = cases.get(cid)
        if not c:
            continue
        a = res.get("results", {}).get(cid, {})
        ar = a.get("result")
        hr, hmsg = human.get(cid, (None, ""))
        if ar in AUTO_RESULTS:
            result, by, msg = ar, "자동화", a.get("message", "")
        elif hr in AUTO_RESULTS:
            result, by, msg = hr, "사람", hmsg
        else:
            result, by, msg = "not_run", "", NOT_RUN_WHY.get(ar, ar)
        if ar in ATTENTION:
            attention.append({"id": cid, "title": c["title"], "kind": ar, "label": ATTENTION[ar], "message": a.get("message", ""),
                              "human_result": hr})
        row = {"id": cid, "title": c["title"], "domain": c.get("domain", ""), "section": c.get("section", ""), "priority": c.get("priority", ""),
               "automation": c.get("automation", ""), "result": result, "by": by, "message": msg,
               "category": sv[cid]["category"], "select_reason": sv[cid]["reason"]}
        rows.append(row)
        if result == "fail":
            hit = [(n, lv) for n, lv in imps if matches(c, n)]
            hint, why = fail_hint(c, hit, feats)
            fails.append(dict(row, last_result=c.get("last_result", ""), source=c.get("source", ""), expected=c.get("expected", []),
                              preconditions=c.get("preconditions", []), steps=c.get("steps", []),
                              impacts=[{"name": n, "level": lv} for n, lv in hit], hint=hint, hint_why=why))
        elif result == "not_run":
            not_run.append({"id": cid, "title": c["title"], "priority": c.get("priority", ""), "automation": c.get("automation", ""), "why": msg})
    cnt = {k: sum(1 for r in rows if r["result"] == k) for k in ("pass", "fail", "not_run")}
    ops = read_json(args.ops) if args.ops else None
    facts = {"release": sel.get("release", res.get("release", "")), "generated_at": now(),
             "inputs": {"results": args.results, "suite": args.suite, "selection": args.selection, "sheet": args.sheet, "ops": args.ops},
             "suite": {"name": st["name"], "version": st["version"]}, "run_at": res.get("run_at", ""), "base_url": res.get("base_url", ""),
             "sheet_url": res.get("sheet_url", ""), "features": sel.get("features", []), "impacts": sel.get("impacts", []),
             "scope": len(rows), "counts": dict(cnt, attention=len(attention)), "cases": rows, "fails": fails, "attention": attention,
             "not_run": not_run, "ops_source": (ops or {}).get("source"), "ops": ops_facts(ops, cases, sv, cfg) if ops else [],
             "summary": None, "fail_reviews": [], "ops_reviews": []}
    write_json(Path(args.out) / "review-facts.json", facts)
    oi = sum(1 for x in facts["ops"] if x["hint"] == "include")
    print(f"✔ review-facts.json: 범위 {len(rows)}개 — Pass {cnt['pass']} · Fail {cnt['fail']} · 결과 없음 {cnt['not_run']} · 살펴볼 것 {len(attention)}"
          + (f" · 운영 결함 {len(facts['ops'])}건(편입 후보 기준 {oi})" if ops else ""))
    for f in fails:
        print(f"  Fail {f['id']}: 원인 후보 {cfg['review']['causes'].get(f['hint'], '정하기 어려움')} — {f['hint_why']}")


# ── render ──
def validate(d, cfg):
    errs = []
    causes, rc, wm = cfg["review"]["causes"], cfg["ops"]["root_causes"], cfg["ops"]["why_missed"]
    s = d.get("summary") or {}
    if not s.get("headline"):
        errs.append("summary.headline 필요")
    elif len(s["headline"]) > 70:
        errs.append(f"summary.headline은 한눈에 읽히게 70자 이내(지금 {len(s['headline'])}자) — 자세한 내용은 points로")
    if not 2 <= len(s.get("points", [])) <= 6:
        errs.append("summary.points 2~6개")
    if s.get("status") not in STATUS:
        errs.append(f"summary.status는 {'/'.join(STATUS)}")
    if s.get("status") == "ready" and d["not_run"]:
        errs.append(f"결과 없는 케이스 {len(d['not_run'])}개가 있는데 status ready — 실행 뒤 다시 검토하거나 needs_work")
    fr = {x.get("id"): x for x in d.get("fail_reviews", [])}
    for f in d["fails"]:
        x = fr.get(f["id"])
        if not x:
            errs.append(f"{f['id']}: Fail인데 fail_reviews에 없음")
            continue
        if x.get("cause") not in causes:
            errs.append(f"{f['id']}: cause는 {'/'.join(causes)}")
        if x.get("confidence") not in CONF:
            errs.append(f"{f['id']}: confidence는 {'/'.join(CONF)}")
        for k in ("reason", "next"):
            if not x.get(k):
                errs.append(f"{f['id']}: {k} 필요")
        if not x.get("evidence"):
            errs.append(f"{f['id']}: evidence(근거) 1개 이상")
        if x.get("cause") == "tc_outdated" and not x.get("basis"):
            errs.append(f"{f['id']}: TC가 정책·명세와 다르다면 basis(문서 위치) 필요")
        if x.get("confidence") == "low" and not x.get("question"):
            errs.append(f"{f['id']}: 확인 필요면 question(누구에게 무엇을) 필요")
    errs += [f"{k}: Fail이 아닌 케이스가 fail_reviews에 있음" for k in fr if k not in {f["id"] for f in d["fails"]}]
    orv = {x.get("key"): x for x in d.get("ops_reviews", [])}
    for o in d["ops"]:
        x = orv.get(o["key"])
        if not x:
            errs.append(f"{o['key']}: 운영 결함인데 ops_reviews에 없음")
            continue
        if x.get("root_cause") not in rc:
            errs.append(f"{o['key']}: root_cause는 {'/'.join(rc)}")
        if x.get("why_missed") not in wm:
            errs.append(f"{o['key']}: why_missed는 {'/'.join(wm)}")
        if x.get("recommendation") not in REC:
            errs.append(f"{o['key']}: recommendation은 {'/'.join(REC)}")
        if not x.get("reason"):
            errs.append(f"{o['key']}: reason 필요")
        if x.get("recommendation") != o["hint"] and not x.get("override_reason"):
            errs.append(f"{o['key']}: 기준표({REC[o['hint']][0]})와 다르게 추천하면 override_reason 필요")
        if x.get("merged_into"):
            tgt = orv.get(x["merged_into"])
            if not tgt or tgt.get("recommendation") != "include" or tgt.get("merged_into"):
                errs.append(f"{o['key']}: merged_into는 회귀 편입 후보(include)이면서 합쳐지지 않은 티켓이어야 함")
        elif x.get("recommendation") == "include":
            c = x.get("case") or {}
            miss = [k for k in ("title", "domain", "section", "steps", "expected", "priority", "automation") if not c.get(k)]
            if miss:
                errs.append(f"{o['key']}: 회귀 케이스 초안(case)에 {miss} 필요")
            if c.get("automation") and c["automation"] not in cfg["suite"]["automation"]:
                errs.append(f"{o['key']}: case.automation은 {'/'.join(cfg['suite']['automation'])}")
    return errs


def chip(t, cls=""):
    return f'<span class="chip {cls}">{e(t)}</span>'


def flag(t, color):
    return f'<span class="flag {color}">{e(t)}</span>'


def page(title, body, meta=""):
    css = (HERE / "templates" / "reg.css").read_text(encoding="utf-8")
    return (f'<!doctype html>\n<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{e(title)}</title><style>{css}</style></head>\n<body><div class=\"wrap\">"
            f'<header class="top"><span class="eyebrow">QA 오케스트라 · 회귀 검토자</span><h1>{e(title)}</h1><div class="meta">{meta}</div></header>'
            f"{body}</div></body></html>\n")


def point_parts(p):
    """요점 한 줄 → (라벨, [줄…]). '라벨: 내용'이면 라벨을 떼고, 내용은 문장('다.')·' — ' 단위로 줄을 나눈다."""
    label, body = (p.split(": ", 1) if ": " in p[:40] else ("", p))
    lines = [x.strip() for x in re.split(r"(?<=다)\.\s+|\s+—\s+", body) if x.strip()]
    return label, lines


def br(text):
    """긴 문장을 문장('다.')·' — '·'. ' 단위로 줄바꿈"""
    return "<br>".join(e(x) for x in re.split(r"(?<=[다)])\.\s+|\s+—\s+|(?<=\))\.\s+", str(text)) if x.strip())


def lst(items):
    return "<br>".join(e(x) for x in items) if isinstance(items, list) else e(items)


def cmd_render(args, cfg):
    d = read_json(args.review)
    errs = validate(d, cfg)
    if errs:
        sys.exit("✘ review.json 점검 실패:\n  - " + "\n  - ".join(errs))
    causes, owners = cfg["review"]["causes"], cfg["review"]["owners"]
    rc, wm = cfg["ops"]["root_causes"], cfg["ops"]["why_missed"]
    out = Path(args.outdir or Path(args.review).parent)
    s, cnt = d["summary"], d["counts"]
    fr = {x["id"]: x for x in d["fail_reviews"]}
    orv = {x["key"]: x for x in d["ops_reviews"]}
    url = d.get("sheet_url")
    st_label, st_color = STATUS[s["status"]]

    # 숫자 칸
    def box(b, label, cls=""):
        if url:
            return f'<a class="kpi link{cls}" href="{e(url)}" target="_blank" rel="noopener"><b>{b}</b><span>{label} ↗</span></a>'
        return f'<div class="kpi{cls}"><b>{b}</b><span>{label}</span></div>'
    inc = [o for o in d["ops"] if orv[o["key"]]["recommendation"] == "include" and not orv[o["key"]].get("merged_into")]
    kpi = ('<div class="kpis">' + box(f'{cnt["pass"] + cnt["fail"]}/{d["scope"]}', "결과 나옴") + box(cnt["pass"], "Pass")
           + box(cnt["fail"], "Fail", " warn" if cnt["fail"] else "") + box(cnt["not_run"], "결과 없음", " caution" if cnt["not_run"] else "")
           + (f'<div class="kpi{" caution" if inc else ""}"><b>{len(inc)}/{len(d["ops"])}</b><span>운영 결함 회귀 후보</span></div>' if d["ops"] else "")
           + "</div>")
    def pt_html(p):
        label, lines = point_parts(p)
        return f'<li>{f"<b>{e(label)}</b>" if label else ""}' + "".join(f"<div>{e(x)}</div>" for x in lines) + "</li>"
    head = (f'<section class="card"><div class="verdict {"ok" if s["status"] == "ready" else "warn"}"><b>{e(s["headline"])}</b></div>'
            f'<ul class="pts">{"".join(pt_html(p) for p in s["points"])}</ul>'
            f'<p class="note">{flag(st_label, st_color)} 원인은 추천이다 — 확정은 담당자가 확인한 뒤 한다.</p></section>')

    # 할 일 (담당별)
    todo = {}
    for f in d["fails"]:
        x = fr[f["id"]]
        who = x.get("owner") or owners[x["cause"]]
        todo.setdefault(who, []).append((f["id"], x["next"]))
        if x.get("question"):
            todo.setdefault(x["question"].get("to", "기획"), []).append((f["id"], x["question"]["ask"]))
    for a in d["attention"]:
        todo.setdefault("회귀 자동화 관리자", []).append((a["id"], f'{a["label"]}: {a["message"].splitlines()[0] if a["message"] else ""}'))
    if d["not_run"]:
        todo.setdefault("테스터", []).append((", ".join(x["id"] for x in d["not_run"]), f'결과 없는 {len(d["not_run"])}개 실행(결과 시트에 기록)'))
    for o in inc:
        todo.setdefault("회귀 TC 관리자", []).append((o["key"], f'운영 결함 회귀 편입 후보 — 승인 받아 넣기{"" if o["closed"] else "(수정 완료 뒤)"}'))
    todo_html = "".join(f'<tr><td><b>{e(w)}</b></td><td>' + "<br>".join(f'<span class="mono">{e(i)}</span> {e(t)}' for i, t in items) + "</td></tr>"
                        for w, items in todo.items())
    todo_sec = f'<section><h2>담당별 할 일</h2><div class="tbl"><table><thead><tr><th>담당</th><th>할 일</th></tr></thead><tbody>{todo_html}</tbody></table></div></section>' if todo else ""

    # Fail 원인
    fail_rows = ""
    for f in d["fails"]:
        x = fr[f["id"]]
        q = x.get("question")
        fail_rows += (f'<tr><td class="mono">{e(f["id"])}<div class="muted small">{e(f["priority"])} · {e(f["by"])}</div></td>'
                      f'<td>{e(f["title"])}<div class="muted small pre">{e(f["message"])}</div></td>'
                      f'<td>{flag(causes[x["cause"]], "red" if x["cause"] in ("change", "existing") else "amber")}'
                      f'<div class="small">{e(CONF[x["confidence"]])}</div></td>'
                      f'<td>{br(x["reason"])}<div class="muted small">근거: {lst(x["evidence"])}</div>'
                      + (f'<div class="muted small">문서: {e(x["basis"])}</div>' if x.get("basis") else "")
                      + (f'<div class="small"><b>질문({e(q.get("to", "기획"))})</b> {e(q["ask"])}</div>' if q else "")
                      + f'</td><td><b>{e(x.get("owner") or owners[x["cause"]])}</b><div class="small">{br(x["next"])}</div></td></tr>')
    fail_sec = (f'<section><h2>Fail 원인 {len(d["fails"])}개</h2><div class="tbl"><table><thead><tr><th>CaseID</th><th>케이스·실제 결과</th><th>원인(추천)</th>'
                f'<th>이유·근거</th><th>다음 할 일</th></tr></thead><tbody>{fail_rows}</tbody></table></div></section>') if d["fails"] else \
        '<section><h2>Fail 없음</h2></section>'

    att = ""
    if d["attention"]:
        att = ('<section><h2>살펴볼 것(결과로 치지 않음)</h2><div class="tbl"><table><thead><tr><th>CaseID</th><th>종류</th><th>내용</th></tr></thead><tbody>'
               + "".join(f'<tr><td class="mono">{e(a["id"])}</td><td>{e(a["label"])}</td><td class="pre">{e(a["message"])}</td></tr>' for a in d["attention"])
               + "</tbody></table></div></section>")
    nr = ""
    if d["not_run"]:
        nr = (f'<section><details><summary>{flag("결과 없음", "amber")} {len(d["not_run"])}개 — 판정 전에 실행 필요</summary><div class="tbl"><table><thead><tr><th>CaseID</th><th>title</th><th class="c">우선</th><th>왜 결과가 없나</th></tr></thead><tbody>'
              + "".join(f'<tr><td class="mono">{e(x["id"])}</td><td>{e(x["title"])}</td><td class="c">{e(x["priority"])}</td><td>{e(x["why"])}</td></tr>' for x in d["not_run"])
              + "</tbody></table></div></details></section>")

    # 운영 결함
    ops_sec = ""
    if d["ops"]:
        rows = ""
        for o in d["ops"]:
            x = orv[o["key"]]
            rec_l, rec_c = REC[x["recommendation"]]
            link = f'<a href="{e(o["url"])}" target="_blank" rel="noopener">{e(o["key"])}</a>' if o.get("url", "").startswith("http") else e(o["key"])
            rel = "<br>".join(f'{e(r["id"])} {e(r["title"])} <span class="muted">({e(r["why"])}, 이번 범위 {e(r["this_release"])})</span>' for r in o["related"][:3]) or "관련 케이스 없음"
            case = x.get("case")
            merged = [k for k, v in orv.items() if v.get("merged_into") == o["key"]]
            draft = (f'<div class="small">{e(x["merged_into"])} 후보에 합침</div>' if x.get("merged_into") else "") + (f'<div class="small"><b>회귀 케이스 초안</b> [{e(case["domain"])} · {e(case["section"])} · {e(case["priority"])} · '
                     f'{e(cfg["suite"]["automation"].get(case["automation"], case["automation"]))}] {e(case["title"])}<br>기대 결과: {lst(case["expected"])}'
                     + (f' {flag("확인 필요 남음", "amber")}' if any(PENDING in str(t) for k in ("preconditions", "steps", "expected") for t in case.get(k, [])) else "")
                     + (f'<br>함께 막는 티켓: {e(", ".join(merged))}' if merged else "") + "</div>" if case else "")
            rows += (f'<tr><td class="mono">{link}<div class="muted small">{e(o.get("severity", ""))} · {e(o.get("status", ""))}</div></td>'
                     f'<td>{e(o["summary"])}<div class="muted small">{e(o.get("area", ""))} · {e(str(o.get("created", ""))[:10])}</div></td>'
                     f'<td>{e(rc[x["root_cause"]])}<div class="small">놓친 이유: {e(wm[x["why_missed"]])}</div></td>'
                     f'<td>{flag(rec_l, rec_c)}<div class="muted small">기준: {e(o["hint_why"])}</div><div class="small">{e(x["reason"])}</div>'
                     + (f'<div class="small">기준과 다름: {e(x["override_reason"])}</div>' if x.get("override_reason") else "")
                     + f'{draft}</td><td class="small">{rel}</td></tr>')
        src = d.get("ops_source") or {}
        ops_sec = (f'<section><h2>운영 결함 원인 분석 {len(d["ops"])}건</h2><p class="note">{e(src.get("site", ""))} · <span class="mono">{e(src.get("jql", ""))}</span> · {e(str(src.get("fetched_at", ""))[:16].replace("T", " "))} (지라는 읽기만 함)</p>'
                   f'<div class="tbl"><table><thead><tr><th>티켓</th><th>내용</th><th>원인·놓친 이유</th><th>회귀 편입</th><th>스위트의 관련 케이스</th></tr></thead><tbody>{rows}</tbody></table></div>'
                   '<p class="note">회귀 편입 후보는 회귀 TC 관리자가 QA 매니저 승인을 받아 넣는다(ops-candidates.json).</p></section>')

    meta = (f'<span>{e(d["generated_at"][:16].replace("T", " "))}</span><span>스위트 {e(d["suite"]["name"])} v{d["suite"]["version"]}</span>'
            f'<span>자동화 실행 {e(d["run_at"][:16].replace("T", " "))}</span>'
            + (f'<span><a href="{e(url)}" target="_blank" rel="noopener">회귀 실행 결과 시트 ↗</a></span>' if url else ""))
    title = f'{d["release"]} 회귀 검토'
    out.mkdir(parents=True, exist_ok=True)
    (out / "review.html").write_text(page(title, head + kpi + todo_sec + fail_sec + att + nr + ops_sec, meta), encoding="utf-8")

    md = [f"# {title}", "", f"**{s['headline']}** ({st_label})", ""]
    for p in s["points"]:
        label, lines = point_parts(p)
        md += ([f"- **{label}**"] + [f"  - {x}" for x in lines]) if label else [f"- {lines[0] if lines else p}"] + [f"  - {x}" for x in lines[1:]]
    md.append("")
    md += [f"범위 {d['scope']}개 — Pass {cnt['pass']} · Fail {cnt['fail']} · 결과 없음 {cnt['not_run']}" + (f" · 결과 시트 {url}" if url else ""), ""]
    if d["fails"]:
        md += ["## Fail 원인", "", "| CaseID | 실제 결과 | 원인(추천) | 이유 | 다음 할 일 |", "|---|---|---|---|---|"]
        for f in d["fails"]:
            x = fr[f["id"]]
            md.append(f"| {f['id']} | {f['message'].splitlines()[0]} | {causes[x['cause']]} ({CONF[x['confidence']]}) | {x['reason']} | {x.get('owner') or owners[x['cause']]}: {x['next']} |")
        md.append("")
    if todo:
        md += ["## 담당별 할 일", ""] + [f"- **{w}**: " + "; ".join(f"{i} {t}" for i, t in items) for w, items in todo.items()] + [""]
    if d["ops"]:
        md += ["## 운영 결함", "", "| 티켓 | 내용 | 원인 | 놓친 이유 | 회귀 편입 |", "|---|---|---|---|---|"]
        for o in d["ops"]:
            x = orv[o["key"]]
            md.append(f"| {o['key']} | {o['summary']} | {rc[x['root_cause']]} | {wm[x['why_missed']]} | {REC[x['recommendation']][0]} — {x['reason']} |")
        md.append("")
    (out / "review.md").write_text("\n".join(md), encoding="utf-8")

    cands = [{"id": f"OPS-{n:02d}", "key": o["key"], "also": [k for k, v in orv.items() if v.get("merged_into") == o["key"]], "url": o.get("url", ""), "summary": o["summary"], "severity": o.get("severity", ""),
              "status": o.get("status", ""), "closed": o["closed"], "reason": orv[o["key"]]["reason"], "case": orv[o["key"]]["case"],
              "confirmed": not any(PENDING in str(t) for k in ("preconditions", "steps", "expected") for t in orv[o["key"]]["case"].get(k, []))}
             for n, o in enumerate(inc, 1)]
    write_json(out / "ops-candidates.json", {"release": d["release"], "review": str(Path(args.review)), "generated_at": now(), "candidates": cands})
    print(f"✔ {out / 'review.html'}, review.md, ops-candidates.json — Fail {len(d['fails'])} · 운영 편입 후보 {len(cands)} · {st_label}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("collect"); p.add_argument("--results", required=True); p.add_argument("--suite", required=True)
    p.add_argument("--selection", required=True); p.add_argument("--sheet"); p.add_argument("--ops"); p.add_argument("--out", required=True)
    p = sub.add_parser("render"); p.add_argument("review"); p.add_argument("outdir", nargs="?")
    for s in sub.choices.values():
        s.add_argument("--config")
    args = ap.parse_args()
    cfg = load_config(args.config)
    {"collect": cmd_collect, "render": cmd_render}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
