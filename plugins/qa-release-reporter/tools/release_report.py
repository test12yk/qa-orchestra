#!/usr/bin/env python3
"""릴리즈 QA 리포터 도구: 릴리즈 판정서와 회귀 검토를 모아(collect) 한 장짜리 릴리즈 QA 리포트를 그린다(render).

사용법:
  python3 release_report.py collect --judgment <release-judgment.json> --review <회귀 검토 review.json>
                                    [--link judgment=<URL> review=<URL> run=<URL> selection=<URL> "feature:<기능>=<URL>"] --out <폴더>
      → <폴더>/report-facts.json  (결정 상태, 릴리즈 이슈·할 일, 기능 판정, 회귀 결과, Fail 원인, 이번 릴리즈 관련 운영 결함, QA 산출물)
  python3 release_report.py render <report.json> [출력 폴더]
      → report.html(위 요약 · 매니저·기획용 / 아래 상세 · 개발·QA용), report.md, report.confluence.html
  python3 release_report.py record <report.json> --where confluence --url <페이지 URL> [--page-id <id>]
      → 올린 위치를 남긴다(다음엔 같은 페이지를 고친다).

경로는 현재 폴더 기준이다. 공통 옵션: --config <qa-orchestra.config.json>
"""
import argparse
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
REF = re.compile(r"(?<![A-Za-z0-9])(?:[A-Z]{1,6}-\d+|R[1-9])(?!\d)")
FEATURE_DECISION = {"include": "편입 가능", "conditional": "조건부 편입", "hold": "편입 보류"}
CONF = {"high": "근거 충분", "mid": "근거 일부", "low": "확인 필요"}
VCLS = {"go": "ok", "go_conditional": "warn", "go_without": "warn", "no_go": "bad"}


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


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def html_of(p, name=None):
    """JSON 산출물 옆에 있는 사람이 읽는 HTML 위치."""
    if not p:
        return ""
    p = Path(p)
    h = p.parent / (name or p.with_suffix(".html").name)
    return str(h) if h.exists() else str(p)


# ── collect ──
def collect(args, cfg):
    rc = cfg["report"]
    jd = read_json(args.judgment)
    rv = read_json(args.review)
    links = args.link
    decs = jd.get("decisions", [])
    dec = decs[-1] if decs else None
    rec = jd["recommendation"]
    exclude = (dec or {}).get("exclude") if dec else rec.get("exclude", [])
    cases = jd.get("selection", {}).get("cases", [])
    scope = [c for c in cases if c["category"] != "skip"]
    res = {c["id"]: c.get("result", "") for c in rv.get("cases", [])}
    by_cat = {}
    for c in scope:
        row = by_cat.setdefault(c["category"], {"pass": 0, "fail": 0, "not_run": 0})
        k = res.get(c["id"]) if res.get(c["id"]) in row else "not_run"
        row[k] += 1
    auto = sum(1 for c in scope if c.get("automation") == "api")
    reviews = {r["id"]: r for r in rv.get("fail_reviews", [])}
    fails = []
    for f in rv.get("fails", []):
        r = reviews.get(f["id"], {})
        fails.append({"id": f["id"], "title": f["title"], "priority": f.get("priority", ""), "automation": f.get("automation", ""),
                      "message": f.get("message", ""), "cause": r.get("cause", ""), "confidence": r.get("confidence", ""),
                      "reason": r.get("reason", ""), "next": r.get("next", ""), "owner": r.get("owner", ""),
                      "question": r.get("question"), "blockers": [b["id"] for b in jd.get("blockers", []) if f["id"] in b.get("refs", [])]})
    # 운영 결함: 이번 릴리즈 관련만 — 아직 열려 있거나(이번 빌드에 수정이 들어가나), 관련 회귀 케이스가 이번 범위에 든 것
    orv = {o["key"]: o for o in rv.get("ops_reviews", [])}
    ops = []
    for o in rv.get("ops", []):
        rel = [x for x in o.get("related", []) if x.get("this_release") not in (None, "", "skip")]
        if o.get("closed") and not rel:
            continue
        r = orv.get(o["key"], {})
        ops.append({"key": o["key"], "url": o.get("url", ""), "severity": o.get("severity", ""), "status": o.get("status", ""), "closed": bool(o.get("closed")),
                    "summary": o.get("summary", ""), "fix": o.get("fix", ""), "root_cause": r.get("root_cause", ""), "recommendation": r.get("recommendation", ""),
                    "merged_into": r.get("merged_into", ""),
                    "why": "아직 열림 — 이번 빌드에 수정이 들어가는지 확인" if not o.get("closed") else "관련 회귀 케이스가 이번 범위에 있음",
                    "related": [{"id": x["id"], "result": res.get(x["id"], "")} for x in rel]})
    ex = [x for x in jd.get("exclusions", []) if x["feature"] in (exclude or [])]
    sheet = rv.get("sheet_url", "") or jd.get("sheet_url", "")
    inp = rv.get("inputs", {})
    outputs = [
        {"step": "회귀 TC 관리자", "name": "릴리즈 회귀 범위", "what": f"스위트 {jd.get('suite', {}).get('cases', '')}개 중 이번 범위 {len(scope)}개(" +
         " · ".join(f"{rc['categories'][k]} {sum(1 for c in scope if c['category'] == k)}" for k in rc["categories"]) + ")",
         "url": links.get("selection", ""), "file": html_of(inp.get("selection"))},
        {"step": "회귀 자동화 관리자", "name": "회귀 실행 결과", "what": f"자동화 {auto}개 실행 결과(한 번 더 돌려 불안정 구분)",
         "url": links.get("run", ""), "file": html_of(inp.get("results"), "run.html")},
        {"step": "회귀 자동화 관리자 · 테스터", "name": "회귀 실행 시트", "what": f"범위 {len(scope)}개의 결과·이슈내용(수동은 테스터가 채움)",
         "url": sheet, "file": inp.get("sheet", "")},
        {"step": "회귀 검토자", "name": "회귀 검토 보고서", "what": f"Fail {len(fails)}개 원인·담당, 운영 결함 {len(rv.get('ops', []))}건 분석",
         "url": links.get("review", ""), "file": html_of(args.review)},
        {"step": "릴리즈 판정관", "name": "릴리즈 판정서", "what": f"추천 {rc['verdicts'][rec['verdict']]}, 릴리즈 이슈 {len(jd.get('blockers', []))}개 · 할 일 {len(jd.get('conditions', []))}개",
         "url": links.get("judgment", ""), "file": html_of(args.judgment)},
    ] + [{"step": "신규 기능 판정관", "name": f"{f['feature']} 판정서", "what": f"편입 {FEATURE_DECISION.get(f['decision'], '결정 전')}",
          "url": links.get(f"feature:{f['feature']}", ""), "file": html_of(f.get("judgment"))} for f in jd.get("features", [])]
    facts = {"release": jd.get("release", ""), "generated_at": now(), "status": "final" if dec else "interim",
             "inputs": {"judgment": args.judgment, "review": args.review}, "decisions_seen": len(decs),
             "recommendation": {k: rec.get(k) for k in ("verdict", "headline", "points", "exclude", "deviation_reason") if rec.get(k)},
             "decision": dec, "exclude": exclude or [], "features": jd.get("features", []),
             "blockers": jd.get("blockers", []), "conditions": jd.get("conditions", []), "alternatives": jd.get("alternatives", []),
             "regression": {"scope": len(scope), "counts": rv.get("counts", {}), "by_category": by_cat, "automation": auto, "manual": len(scope) - auto,
                            "sheet_url": sheet, "suite": jd.get("suite", {}), "run_at": rv.get("run_at", "")},
             "fails": fails, "not_run": rv.get("not_run", []), "attention": rv.get("attention", []),
             "rerun": [{"feature": x["feature"], "file": x["file"], "rerun": len(x["rerun"]), "auto": sum(1 for r in x["rerun"] if r["automation"] == "api"),
                        "keep": len(x["keep"]), "drop": len(x["drop"])} for x in ex],
             "ops": ops, "ops_total": len(rv.get("ops", [])), "outputs": outputs, "published": []}
    write_json(Path(args.out) / "report-facts.json", facts)
    kind = "릴리즈 완료 리포트" if dec else "중간 리포트 · 결정 대기"
    print(f"report-facts.json: {kind} — 추천 {rc['verdicts'][rec['verdict']]}"
          + (f" · 결정 {rc['verdicts'][dec['decision']]}" if dec else "")
          + f" · 범위 {len(scope)} · Fail {len(fails)} · 결과 없음 {len(facts['not_run'])} · 이번 릴리즈 관련 운영 결함 {len(ops)}/{len(rv.get('ops', []))}")


# ── render ──
e = lambda s: html.escape(str(s if s is not None else ""))


def point_parts(p):
    label, body = (p.split(": ", 1) if ": " in p[:40] else ("", p))
    lines = [x.strip() for x in re.split(r"(?<=다)\.\s+|\s+—\s+", body) if x.strip()]
    return label, lines


def validate(r, cfg):
    errs = []
    s = r.get("summary") or {}
    if not s.get("headline") or len(s["headline"]) > 70:
        errs.append("summary.headline: 70자 이내 한 줄")
    pts = s.get("points") or []
    if not 2 <= len(pts) <= 6:
        errs.append("summary.points는 2~6개('<라벨>: <내용>')")
    for i, p in enumerate(pts):
        if not REF.search(p):
            errs.append(f"summary.points[{i}]에 근거 번호(B-·K-·RG-·지라 키·R 요소)가 없음: {p[:40]}")
    if r.get("status") not in ("interim", "final"):
        errs.append("status는 interim/final")
    if r.get("status") == "final" and not r.get("decision"):
        errs.append("완료 리포트(final)인데 사람의 결정이 없음 — 결정 전이면 interim")
    if r.get("status") == "interim" and r.get("decision"):
        errs.append("결정이 있는데 중간 리포트(interim) — collect를 다시 돌린다")
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
    return "" if n == r.get("decisions_seen", 0) else f"릴리즈 판정서에 이 리포트 이후 결정이 {n - r.get('decisions_seen', 0)}건 더 기록됨 — collect를 다시 돌릴 것"


def ex_txt(xs):
    return f" — {', '.join(xs)} 빼기" if xs else ""


def decision_line(r, vl):
    dec, rec = r.get("decision"), r["recommendation"]
    if dec:
        return (f"{vl[dec['decision']]}{ex_txt(dec.get('exclude'))} — {dec['by']}, {dec['at'][:16].replace('T', ' ')}" + (f" — {dec['note']}" if dec.get("note") else "")
                + (f" (남긴 채 배포: {', '.join(dec['conditions_accepted'])})" if dec.get("conditions_accepted") else "")
                + (f" · 판정관 추천은 {vl[rec['verdict']]}" if dec["decision"] != rec["verdict"] else ""))
    return f"결정 대기 — 릴리즈 판정관 추천 {vl[rec['verdict']]}{ex_txt(rec.get('exclude'))}"


def render(path, outdir, cfg):
    r = read_json(path)
    errs = validate(r, cfg)
    if errs:
        sys.exit("report.json 점검 실패:\n  - " + "\n  - ".join(errs))
    note = stale_note(r)
    if note:
        print("경고: " + note)
    rc = cfg["report"]
    vl, owners, causes = rc["verdicts"], rc["owners"], rc["causes"]
    rec, dec, s = r["recommendation"], r.get("decision"), r["summary"]
    final = r["status"] == "final"
    kind = "릴리즈 완료 리포트" if final else "중간 리포트 · 결정 대기"
    title = f"{r['release']} 릴리즈 QA 리포트"
    v = dec["decision"] if dec else rec["verdict"]
    rg = r["regression"]
    cnt = rg["counts"]
    bl = r.get("blockers", [])
    bmap = {b["id"]: b for b in bl}
    n_block = sum(1 for b in bl if b["level"] == "block")
    accepted = set((dec or {}).get("conditions_accepted", []))
    out = Path(outdir or Path(path).parent)
    out.mkdir(parents=True, exist_ok=True)
    links = r.get("links", [])

    pts = []
    for p in s["points"]:
        label, lines = point_parts(p)
        pts.append("<li>" + (f"<b>{e(label)}</b>" if label else "") + "".join(f"<div>{e(x)}</div>" for x in lines) + "</li>")
    blocks = [b for b in bl if b["level"] == "block"]
    keyb = ("<ul class=\"keybugs\">" + "".join(f'<li><span class="flag red">릴리즈 불가</span> <span class="mono">{e(b["id"])}</span> {e(b["title"])}'
                                              + "".join(f' <span class="feat">관련 기능: {e(x)}</span>' for x in b.get("features", [])) + "</li>" for b in blocks) + "</ul>") if blocks else ""
    if dec:
        dline = f'<b class="inline {VCLS[v]}">{e(vl[v])}{e(ex_txt(dec.get("exclude")))}</b> — {e(dec["by"])}, {e(dec["at"][:16].replace("T", " "))}' + (f' — {e(dec["note"])}' if dec.get("note") else "")
    else:
        dline = f'<b class="inline">결정 대기</b> — 릴리즈 판정관 추천 <b class="inline {VCLS[v]}">{e(vl[v])}{e(ex_txt(rec.get("exclude")))}</b>'

    def box(n, label, cls="", href=""):
        if href:
            return f'<a class="kpi link{cls}" href="{e(href)}" target="_blank" rel="noopener"><b>{e(n)}</b><span>{e(label)}</span></a>'
        return f'<div class="kpi{cls}"><b>{e(n)}</b><span>{e(label)}</span></div>'
    feats = r.get("features", [])
    decided = sum(1 for f in feats if f.get("decision") in ("include", "conditional"))
    kpis = ('<div class="kpis">' + box(f"{decided}/{len(feats)}", "기능 편입 결정", " caution" if decided < len(feats) else "")
            + box(f"{cnt.get('pass', 0)}/{rg['scope']}", "회귀 Pass · 결과 시트" if rg["sheet_url"] else "회귀 Pass", "", rg["sheet_url"])
            + box(cnt.get("fail", 0), "회귀 Fail", " warn" if cnt.get("fail") else "")
            + box(cnt.get("not_run", 0), "결과 없음", " caution" if cnt.get("not_run") else "")
            + box(n_block, "릴리즈 불가 이슈", " warn" if n_block else "") + "</div>")

    crow = []
    for c in r.get("conditions", []):
        lv = [bmap[b]["level"] for b in c.get("resolves", []) if b in bmap]
        tag = '<span class="flag red">릴리즈 불가</span>' if "block" in lv else '<span class="flag amber">조건부</span>' if lv else ""
        acc = ' <span class="flag green">남긴 채 배포</span>' if c["id"] in accepted else ""
        crow.append(f'<tr><td class="mono">{e(c["id"])}</td><td>{e(c["what"])}{acc}<div class="muted small">확인: {e(c["verify"])}</div></td>'
                    f'<td>{e(owners.get(c["owner"], c["owner"]))}</td><td>{tag} <span class="mono small">{e(", ".join(c.get("resolves", [])))}</span></td></tr>')
    todo_html = (f'<section id="todo"><h2>배포 전에 할 일 <span class="muted small">{len(crow)}개</span></h2><div class="tbl"><table><thead><tr><th>번호</th><th>할 일</th><th>담당</th><th>해결하는 이슈</th></tr></thead>'
                 f'<tbody>{"".join(crow)}</tbody></table></div></section>') if crow else ""
    alt = r.get("alternatives", [])
    alt_html = ""
    if alt and not final:
        alt_html = ('<section id="alternatives"><h2>다른 선택지 <span class="muted small">QA 매니저가 고를 수 있는 것</span></h2><div class="grid2">'
                    + "".join(f'<div class="card"><h3>{e(vl[a["verdict"]])}{e(ex_txt(a.get("exclude")))}</h3><p class="muted small">{e(a["option"])}</p>'
                              f'<p><b class="good">장점</b> {e(a.get("pros", ""))}</p><p><b class="bad">단점</b> {e(a.get("cons", ""))}</p></div>' for a in alt) + "</div></section>")

    # 상세
    fe_rows = "".join(f'<tr><td><b>{e(f["feature"])}</b></td><td>{e(FEATURE_DECISION.get(f.get("decision"), "결정 전"))}</td>'
                      f'<td>{e(FEATURE_DECISION.get(f.get("recommendation"), "—"))}</td><td>{e(f.get("decided_by", ""))}</td>'
                      f'<td>{e(", ".join(c["id"] for c in f.get("accepted", [])))}</td></tr>' for f in feats)
    fe_html = (f'<section id="features"><h2>이번 릴리즈 기능 <span class="muted small">신규 기능 판정</span></h2><div class="tbl"><table><thead><tr><th>기능</th><th>편입 결정</th><th>판정관 추천</th><th>정한 사람</th><th>받아들인 조건</th></tr></thead>'
               f'<tbody>{fe_rows}</tbody></table></div></section>') if feats else ""
    cats = rc["categories"]
    cat_rows = "".join(f'<tr><td>{e(cats.get(k, k))}</td><td class="c">{sum(row.values())}</td><td class="c green-t">{row["pass"]}</td><td class="c red-t">{row["fail"]}</td><td class="c amber-t">{row["not_run"]}</td></tr>'
                       for k, row in sorted(rg["by_category"].items(), key=lambda kv: list(cats).index(kv[0]) if kv[0] in cats else 9))
    sheet_a = f' <a class="small" href="{e(rg["sheet_url"])}" target="_blank" rel="noopener">결과 시트 열기 ↗</a>' if rg["sheet_url"] else ""
    reg_html = (f'<section id="regression"><h2>회귀 결과 <span class="muted small">범위 {rg["scope"]}개 · 자동화 {rg["automation"]} · 수동 {rg["manual"]}</span>{sheet_a}</h2>'
                f'<div class="tbl"><table><thead><tr><th>범위</th><th class="c">케이스</th><th class="c">Pass</th><th class="c">Fail</th><th class="c">결과 없음</th></tr></thead><tbody>{cat_rows}</tbody></table></div></section>')
    f_rows = "".join(
        f'<tr><td class="mono">{e(f["id"])}</td><td>{e(f["title"])}<div class="muted small pre">{e(f["message"])}</div></td><td class="c">{e(f["priority"])}</td>'
        f'<td>{e(causes.get(f["cause"], "검토 전"))}<div class="muted small">{e(CONF.get(f["confidence"], ""))}</div></td>'
        f'<td>{e(f["next"])}' + (f'<div class="muted small">질문({e(f["question"]["to"])}): {e(f["question"]["ask"])}</div>' if f.get("question") else "") + '</td>'
        f'<td class="mono small">{e(", ".join(f["blockers"]))}</td></tr>' for f in r["fails"])
    fail_html = (f'<section id="fails"><h2>회귀 Fail <span class="muted small">{len(r["fails"])}개 · 원인은 회귀 검토자 추천</span></h2><div class="tbl"><table><thead><tr><th>CaseID</th><th>케이스 · 실제 결과</th><th class="c">우선</th><th>원인(추천)</th><th>다음 할 일</th><th>이슈</th></tr></thead>'
                 f'<tbody>{f_rows}</tbody></table></div></section>') if f_rows else ""
    nr = r.get("not_run", [])
    nr_html = (f'<section id="notrun"><details><summary><span class="flag amber">결과 없음</span> {len(nr)}개</summary><div class="tbl"><table><thead><tr><th>CaseID</th><th>title</th><th class="c">우선</th><th>왜</th></tr></thead><tbody>'
               + "".join(f'<tr><td class="mono">{e(x["id"])}</td><td>{e(x["title"])}</td><td class="c">{e(x["priority"])}</td><td>{e(x["why"])}</td></tr>' for x in nr)
               + "</tbody></table></div></details></section>") if nr else ""
    rr_html = "".join(f'<section id="rerun"><h2>{e(x["feature"])} 뺀 빌드 재회귀</h2><div class="card split"><span>다시 돌림 <b>{x["rerun"]}</b>(자동화 {x["auto"]} · 수동 {x["rerun"] - x["auto"]})</span>'
                      f'<span>결과 그대로 <b>{x["keep"]}</b></span><span>빠짐 <b>{x["drop"]}</b></span><span class="muted mono small">{e(x["file"])}</span></div></section>' for x in r.get("rerun", []))
    rcs, recs = rc["root_causes"], rc["ops_recommendations"]
    o_rows = "".join(
        f'<tr><td class="mono">' + (f'<a href="{e(o["url"])}">{e(o["key"])}</a>' if o.get("url") else e(o["key"])) + f'</td><td>{e(o["severity"])}</td><td>{e(o["status"])}</td>'
        f'<td>{e(o["summary"])}<div class="muted small">{e(o["why"])}' + (" · 관련 케이스 " + e(", ".join(f'{x["id"]} {x["result"] or "결과 없음"}' for x in o["related"])) if o["related"] else "") + '</div></td>'
        f'<td>{e(rcs.get(o["root_cause"], o["root_cause"] or "—"))}</td><td>{e(recs.get(o["recommendation"], "—"))}' + (f' <span class="muted small">({e(o["merged_into"])}에 합침)</span>' if o.get("merged_into") else "") + '</td></tr>'
        for o in r.get("ops", []))
    ops_html = (f'<section id="ops"><h2>이번 릴리즈 관련 운영 결함 <span class="muted small">{len(r["ops"])}건 / 지난 90일 {r["ops_total"]}건</span></h2><div class="tbl"><table><thead><tr><th>키</th><th>심각도</th><th>상태</th><th>내용 · 관련</th><th>원인</th><th>회귀</th></tr></thead>'
                f'<tbody>{o_rows}</tbody></table></div></section>') if o_rows else ""
    out_rows = "".join(f'<tr><td>{e(o["step"])}</td><td><b>{e(o["name"])}</b><div class="muted small">{e(o["what"])}</div></td><td>'
                       + (f'<a href="{e(o["url"])}" target="_blank" rel="noopener">열기 ↗</a>' if o.get("url") else f'<span class="muted small mono">{e(o.get("file", ""))}</span>') + "</td></tr>"
                       for o in r.get("outputs", []))
    outs_html = f'<section id="outputs"><h2>QA 산출물</h2><div class="tbl"><table><thead><tr><th>만든 역할</th><th>산출물</th><th>보기</th></tr></thead><tbody>{out_rows}</tbody></table></div></section>'
    link_html = ('<section id="links"><h2>그 밖의 링크</h2><ul class="ev">' + "".join(f'<li><a href="{e(l["url"])}">{e(l["label"])}</a></li>' for l in links) + "</ul></section>") if links else ""
    pub = "".join(f'<span>{e(p["where"])}: <a href="{e(p["url"])}">{e(p["url"])}</a></span>' for p in r.get("published", []))

    css = (HERE / "templates" / "release-report.css").read_text(encoding="utf-8")
    doc = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title><style>{css}</style></head>
<body><div class="wrap">
<header class="top"><span class="eyebrow">QA 오케스트라 · 릴리즈 QA 리포터</span><h1>{e(title)}</h1>
<div class="meta"><span class="kind {'ok' if final else 'pending'}">{e(kind)}</span><span>{e(r["generated_at"][:16].replace("T", " "))}</span>{pub}</div></header>
<div class="part">요약 · 매니저·기획용</div>
<section id="summary"><div class="verdict {VCLS[v]}"><span class="eyebrow">{'QA 매니저 결정' if final else '결정 대기'}</span><p class="lead">{e(s["headline"])}</p>{keyb}<ul class="pts">{"".join(pts)}</ul>
<p class="small">{dline}</p></div></section>
{kpis}
{todo_html}
{alt_html}
<div class="part">상세 · 개발·QA용</div>
{fe_html}
{reg_html}
{fail_html}
{nr_html}
{rr_html}
{ops_html}
{outs_html}
{link_html}
<p class="muted small">리포터는 앞 단계 결과를 바꾸지 않고 모아 전한다. 배포 여부는 QA 매니저가 정한다.</p>
</div></body></html>
"""
    (out / "report.html").write_text(doc, encoding="utf-8")
    (out / "report.md").write_text(to_md(r, cfg, title, kind), encoding="utf-8")
    (out / "report.confluence.html").write_text(to_confluence(r, cfg, kind), encoding="utf-8")
    print(f"리포트: {kind} → {out / 'report.html'}, report.md, report.confluence.html")


def to_md(r, cfg, title, kind):
    rc = cfg["report"]
    vl, owners, causes = rc["verdicts"], rc["owners"], rc["causes"]
    rg, cnt = r["regression"], r["regression"]["counts"]
    bmap = {b["id"]: b for b in r.get("blockers", [])}
    md = [f"# {title}", "", f"_{kind} · {r['generated_at'][:16].replace('T', ' ')}_", "", "## 요약", "", f"**{r['summary']['headline']}**", ""]
    md += [f"- **[릴리즈 불가] {b['id']}** {b['title']}" for b in r.get("blockers", []) if b["level"] == "block"]
    md += [""]
    for p in r["summary"]["points"]:
        label, lines = point_parts(p)
        md += [f"- **{label}**" if label else f"- {lines.pop(0)}"] + [f"  - {x}" for x in lines]
    md += ["", f"**QA 매니저 결정:** {decision_line(r, vl)}", "",
           f"회귀 범위 {rg['scope']}개 — Pass {cnt.get('pass', 0)} · Fail {cnt.get('fail', 0)} · 결과 없음 {cnt.get('not_run', 0)}" + (f" — [결과 시트]({rg['sheet_url']})" if rg["sheet_url"] else "")]
    if r.get("conditions"):
        md += ["", "## 배포 전에 할 일", "", "| 번호 | 할 일 | 담당 | 해결하는 이슈 |", "|---|---|---|---|"]
        for c in r["conditions"]:
            lv = [bmap[b]["level"] for b in c.get("resolves", []) if b in bmap]
            md.append(f"| {c['id']} | {c['what']} | {owners.get(c['owner'], c['owner'])} | " + ("릴리즈 불가 " if "block" in lv else "조건부 " if lv else "") + ", ".join(c.get("resolves", [])) + " |")
    md += ["", "---", "", "## 이번 릴리즈 기능", ""] + [f"- {f['feature']}: {FEATURE_DECISION.get(f.get('decision'), '결정 전')} (판정관 추천 {FEATURE_DECISION.get(f.get('recommendation'), '—')})" for f in r.get("features", [])]
    if r["fails"]:
        md += ["", "## 회귀 Fail", "", "| CaseID | 케이스 | 우선 | 원인(추천) | 다음 할 일 |", "|---|---|---|---|---|"]
        md += [f"| {f['id']} | {f['title']} | {f['priority']} | {causes.get(f['cause'], '검토 전')} ({CONF.get(f['confidence'], '')}) | {f['next']} |" for f in r["fails"]]
    if r.get("not_run"):
        md += ["", f"**결과 없음 {len(r['not_run'])}개:** " + ", ".join(x["id"] for x in r["not_run"])]
    for x in r.get("rerun", []):
        md += ["", f"**{x['feature']} 뺀 빌드 재회귀:** 다시 돌림 {x['rerun']}(자동화 {x['auto']}) · 결과 그대로 {x['keep']} · 빠짐 {x['drop']} — `{x['file']}`"]
    if r.get("ops"):
        md += ["", "## 이번 릴리즈 관련 운영 결함", "", "| 키 | 심각도 | 상태 | 내용 | 회귀 |", "|---|---|---|---|---|"]
        md += [f"| {o['key']} | {o['severity']} | {o['status']} | {o['summary']} ({o['why']}) | {rc['ops_recommendations'].get(o['recommendation'], '—')} |" for o in r["ops"]]
    md += ["", "## QA 산출물", ""] + [f"- **{o['name']}** ({o['step']}) — {o['what']} — " + (f"[열기]({o['url']})" if o.get("url") else f"`{o.get('file', '')}`") for o in r.get("outputs", [])]
    if r.get("links"):
        md += ["", "## 그 밖의 링크", ""] + [f"- [{l['label']}]({l['url']})" for l in r["links"]]
    return "\n".join(md) + "\n"


def to_confluence(r, cfg, kind):
    """컨플루언스 페이지 본문(Atlassian HTML 형식 조각). 패널·상태 표시를 쓴다."""
    rc = cfg["report"]
    vl, owners, causes = rc["verdicts"], rc["owners"], rc["causes"]
    dec, rec = r.get("decision"), r["recommendation"]
    rg, cnt = r["regression"], r["regression"]["counts"]
    bmap = {b["id"]: b for b in r.get("blockers", [])}
    color = {"go": "green", "go_conditional": "yellow", "go_without": "yellow", "no_go": "red"}
    lozenge = lambda text, c: f'<span data-type="status" data-color="{c}">{e(text)}</span>'
    v = dec["decision"] if dec else rec["verdict"]
    panel = {"go": "success", "go_conditional": "warning", "go_without": "warning", "no_go": "error"}[v] if dec else "info"
    out = [f'<div data-type="panel-{panel}"><p><strong>{e(kind)}</strong> · {lozenge(vl[v] if dec else "결정 대기", color[v] if dec else "neutral")}</p><p>{e(r["summary"]["headline"])}</p></div>']
    out += [f'<p>{lozenge("릴리즈 불가", "red")} {e(b["id"])} {e(b["title"])}</p>' for b in r.get("blockers", []) if b["level"] == "block"]
    items = ""
    for p in r["summary"]["points"]:
        label, lines = point_parts(p)
        items += "<li>" + (f"<p><strong>{e(label)}</strong></p>" if label else "") + "".join(f"<p>{e(x)}</p>" for x in lines) + "</li>"
    out.append(f"<h2>요약</h2><ul>{items}</ul>")
    out.append(f'<p><strong>QA 매니저 결정:</strong> {e(decision_line(r, vl))}</p>')
    out.append((f'<p><a href="{e(rg["sheet_url"])}">회귀 범위 {rg["scope"]}개</a>' if rg["sheet_url"] else f'<p>회귀 범위 {rg["scope"]}개')
               + f' · Pass {cnt.get("pass", 0)} · Fail {cnt.get("fail", 0)} · 결과 없음 {cnt.get("not_run", 0)}</p>')
    if r.get("conditions"):
        rows = ""
        for c in r["conditions"]:
            lv = [bmap[b]["level"] for b in c.get("resolves", []) if b in bmap]
            tag = lozenge("릴리즈 불가", "red") if "block" in lv else lozenge("조건부", "yellow") if lv else ""
            rows += (f'<tr><td>{e(c["id"])}</td><td><p>{e(c["what"])}</p><p>확인: {e(c["verify"])}</p></td>'
                     f'<td>{e(owners.get(c["owner"], c["owner"]))}</td><td><p>{tag} {e(", ".join(c.get("resolves", [])))}</p></td></tr>')
        out.append(f"<h2>배포 전에 할 일</h2><table><thead><tr><th>번호</th><th>할 일</th><th>담당</th><th>해결하는 이슈</th></tr></thead><tbody>{rows}</tbody></table>")
    out.append("<hr><h2>이번 릴리즈 기능</h2><ul>" + "".join(f'<li>{e(f["feature"])}: {e(FEATURE_DECISION.get(f.get("decision"), "결정 전"))} (판정관 추천 {e(FEATURE_DECISION.get(f.get("recommendation"), "—"))})</li>' for f in r.get("features", [])) + "</ul>")
    if r["fails"]:
        out.append("<h2>회귀 Fail</h2><table><thead><tr><th>CaseID</th><th>케이스</th><th>우선</th><th>원인(추천)</th><th>다음 할 일</th></tr></thead><tbody>"
                   + "".join(f'<tr><td>{e(f["id"])}</td><td><p>{e(f["title"])}</p><p>{e(f["message"])}</p></td><td>{e(f["priority"])}</td>'
                             f'<td>{e(causes.get(f["cause"], "검토 전"))} ({e(CONF.get(f["confidence"], ""))})</td><td>{e(f["next"])}</td></tr>' for f in r["fails"]) + "</tbody></table>")
    if r.get("not_run"):
        out.append(f'<p><strong>결과 없음 {len(r["not_run"])}개:</strong> {e(", ".join(x["id"] for x in r["not_run"]))}</p>')
    for x in r.get("rerun", []):
        out.append(f'<p><strong>{e(x["feature"])} 뺀 빌드 재회귀:</strong> 다시 돌림 {x["rerun"]}(자동화 {x["auto"]}) · 결과 그대로 {x["keep"]} · 빠짐 {x["drop"]}</p>')
    if r.get("ops"):
        out.append("<h2>이번 릴리즈 관련 운영 결함</h2><table><thead><tr><th>키</th><th>심각도</th><th>상태</th><th>내용</th><th>회귀</th></tr></thead><tbody>"
                   + "".join(f'<tr><td>' + (f'<a href="{e(o["url"])}" data-card-appearance="inline"></a>' if o.get("url") else e(o["key"])) + f'</td><td>{e(o["severity"])}</td><td>{e(o["status"])}</td>'
                             f'<td><p>{e(o["summary"])}</p><p>{e(o["why"])}</p></td><td>{e(rc["ops_recommendations"].get(o["recommendation"], "—"))}</td></tr>' for o in r["ops"]) + "</tbody></table>")
    out.append("<h2>QA 산출물</h2><table><thead><tr><th>만든 역할</th><th>산출물</th><th>보기</th></tr></thead><tbody>"
               + "".join(f'<tr><td>{e(o["step"])}</td><td><p><strong>{e(o["name"])}</strong></p><p>{e(o["what"])}</p></td><td>'
                         + (f'<a href="{e(o["url"])}">열기</a>' if o.get("url") else f'링크 없음 · <code>{e(o.get("file", ""))}</code>') + "</td></tr>" for o in r.get("outputs", []))
               + "</tbody></table>")
    if r.get("links"):
        out.append("<h2>그 밖의 링크</h2><ul>" + "".join(f'<li><a href="{e(l["url"])}">{e(l["label"])}</a></li>' for l in r["links"]) + "</ul>")
    out.append(f'<p style="font-size: 14px">QA 오케스트라 릴리즈 QA 리포터 · {e(r["generated_at"][:16].replace("T", " "))}</p>')
    return "\n".join(out) + "\n"


def record(args, cfg):
    r = read_json(args.report)
    if not args.url.startswith(("https://", "http://")):
        sys.exit("url은 http(s) 주소")
    pubs = [p for p in r.get("published", []) if p.get("where") != args.where]
    pubs.append({k: v for k, v in {"where": args.where, "url": args.url, "page_id": args.page_id, "at": now(), "status": r.get("status")}.items() if v})
    r["published"] = pubs
    write_json(args.report, r)
    print(f"올린 위치 기록: {args.where} {args.url}")
    render(args.report, None, cfg)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--judgment", required=True)
    c.add_argument("--review", required=True)
    c.add_argument("--link", nargs="*", default=[], help="산출물 링크: judgment|review|run|selection|feature:<기능>=<URL>")
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
        args.link = dict(x.split("=", 1) for x in args.link if "=" in x)
        collect(args, cfg)
    elif args.cmd == "render":
        render(args.report, args.outdir, cfg)
    else:
        record(args, cfg)


if __name__ == "__main__":
    main()
