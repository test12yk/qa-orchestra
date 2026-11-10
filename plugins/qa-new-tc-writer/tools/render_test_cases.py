#!/usr/bin/env python3
"""신규 TC 작성가가 쓴 test-cases.json을 점검하고 CSV(엑셀용)·HTML·마크다운으로 낸다.

사용법: python3 render_test_cases.py <test-cases.json> [출력 폴더] [--analysis plan-analysis.json] [--config 설정.json]
출력: test-cases.csv, test-cases.html, test-cases.md

점검(하나라도 걸리면 파일을 만들지 않고 종료 코드 1):
- TC 필수 필드, 우선순위·유형 값, TC ID 중복
- --analysis 를 주면: 모든 요구사항이 TC 또는 not_covered(이유)로 처리됐는지,
  없는 REQ·질문 번호를 참조하지 않는지
"""
import csv
import html
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
CSS = (HERE / "templates" / "test_cases.css").read_text(encoding="utf-8")
REQUIRED = ["id", "req_ids", "group", "title", "type", "priority", "steps", "expected"]

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


def check(d, cfg, analysis):
    errs = []
    types, prios = set(cfg["tc"]["types"]), set(cfg["tc"]["priorities"])
    ids = Counter(c.get("id") for c in d["cases"])
    errs += [f"TC ID 중복: {i}" for i, n in ids.items() if n > 1]
    for c in d["cases"]:
        miss = [k for k in REQUIRED if not c.get(k)]
        if miss:
            errs.append(f"{c.get('id', '?')}: 필수 필드 없음 {miss}")
        if c.get("priority") not in prios:
            errs.append(f"{c.get('id')}: 우선순위 '{c.get('priority')}'는 설정에 없음 {sorted(prios)}")
        if c.get("type") not in types:
            errs.append(f"{c.get('id')}: 유형 '{c.get('type')}'는 설정에 없음 {sorted(types)}")
    if analysis:
        req_ids = [r["id"] for r in analysis["requirements"]]
        q_ids = {q["id"] for q in analysis.get("questions", [])}
        covered = {r for c in d["cases"] for r in c.get("req_ids", [])}
        excused = {x["req_id"] for x in d.get("not_covered", []) if x.get("reason")}
        errs += [f"{r}: TC도 없고 not_covered 이유도 없음" for r in req_ids if r not in covered and r not in excused]
        errs += [f"{c['id']}: 분석서에 없는 요구사항 {r}" for c in d["cases"] for r in c.get("req_ids", []) if r not in req_ids]
        errs += [f"{c['id']}: 분석서에 없는 질문 {q}" for c in d["cases"] for q in c.get("assumptions", []) if q not in q_ids]
    return errs


def cell(c, key):
    v = c.get(key, "")
    if key == "steps":
        return "\n".join(f"{i}. {s}" for i, s in enumerate(v, 1))
    if isinstance(v, list):
        return "\n".join(f"- {x}" for x in v) if key in ("preconditions", "expected") else ", ".join(map(str, v))
    return str(v)


def write_csv(d, cfg, path):
    cols = cfg["tc"]["columns"]
    # utf-8-sig: 엑셀에서 한글이 깨지지 않게 BOM을 붙인다
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([c["label"] for c in cols])
        for tc in d["cases"]:
            w.writerow([cell(tc, c["key"]) for c in cols])


def coverage_rows(d, analysis):
    by_req = defaultdict(list)
    for c in d["cases"]:
        for r in c["req_ids"]:
            by_req[r].append(c)
    excused = {x["req_id"]: x["reason"] for x in d.get("not_covered", [])}
    if analysis:
        reqs = [(r["id"], r.get("keyword", ""), r.get("text", "")) for r in analysis["requirements"]]
    else:
        reqs = [(r, "", "") for r in sorted(by_req)]
    return reqs, by_req, excused


def render_html(d, cfg, analysis):
    f = d["feature"]
    cases = d["cases"]
    reqs, by_req, excused = coverage_rows(d, analysis)
    covered = sum(1 for r, _, _ in reqs if by_req.get(r))
    pct = round(covered * 100 / len(reqs)) if reqs else 0
    p0 = sum(1 for c in cases if c["priority"] == "P0")
    as_cases = [c for c in cases if c.get("assumptions")]
    auto = sum(1 for c in cases if c.get("automation") == "후보")
    prios = list(cfg["tc"]["priorities"])
    types = [t for t in cfg["tc"]["types"] if any(c["type"] == t for c in cases)]

    cov = []
    for rid, kw, text in reqs:
        cs = by_req.get(rid, [])
        chips = "".join(f'<span class="chip{" p0" if c["priority"] == "P0" else ""}">{e(c["id"])} {e(c["type"])}</span>' for c in cs)
        if not cs:
            chips = f'<span class="muted">{e(excused.get(rid, "TC 없음"))}</span>'
        cov.append(f'<tr class="{"" if cs else "miss"}"><td class="mono">{e(rid)}</td><td><b>{e(kw)}</b><span class="quote">{e(text)}</span></td>'
                   f'<td class="n">{len(cs)}</td><td><div class="chips">{chips}</div></td></tr>')

    cnt = Counter((c["priority"], c["type"]) for c in cases)
    dist = "<tr><th></th>" + "".join(f"<th>{e(t)}</th>" for t in types) + "<th>합계</th></tr>"
    for p in prios:
        row = [cnt[(p, t)] for t in types]
        dist += f'<tr><th title="{e(cfg["tc"]["priorities"][p])}">{p}</th>' + "".join(f'<td class="{"z" if n == 0 else ""}">{n or "·"}</td>' for n in row) + f"<td><b>{sum(row)}</b></td></tr>"

    qs = {q["id"]: q for q in (analysis or {}).get("questions", [])}
    by_q = defaultdict(list)
    for c in as_cases:
        for q in c["assumptions"]:
            by_q[q].append(c["id"])
    as_rows = "".join(
        f'<tr><td class="mono">{e(q)}</td><td>{e(qs.get(q, {}).get("question", ""))}<span class="quote">임시 가정: {e(qs.get(q, {}).get("assumption", ""))}</span></td>'
        f'<td>{e(qs.get(q, {}).get("ask_to", ""))}</td><td><div class="chips">{"".join(f"<span class=chip>{e(t)}</span>" for t in ids)}</div></td></tr>'
        for q, ids in sorted(by_q.items(), key=lambda x: (len(x[0]), x[0])))

    groups = defaultdict(list)
    for c in cases:
        groups[c["group"]].append(c)
    tcs = []
    for g, cs in groups.items():
        tcs.append(f'<div class="group-h">{e(g)} <span class="muted mono">{len(cs)}</span></div>')
        for c in cs:
            pre = "".join(f"<li>{e(x)}</li>" for x in c.get("preconditions", []))
            steps = "".join(f"<li>{e(x)}</li>" for x in c["steps"])
            exp = "".join(f"<li>{e(x)}</li>" for x in c["expected"])
            flags = f'<span class="flag {"red" if c["priority"] == "P0" else "amber" if c["priority"] == "P1" else "green"}">{e(c["priority"])}</span>'
            flags += "".join(f'<span class="chip as">가정 {e(q)}</span>' for q in c.get("assumptions", []))
            tcs.append(f'''<article class="tc" data-p="{e(c["priority"])}" data-a="{"1" if c.get("assumptions") else "0"}">
  <div class="tc-head"><span class="tid">{e(c["id"])}</span><b>{e(c["title"])}</b>{flags}<span class="chip">{e(c["type"])}</span></div>
  <div class="tc-body">
    <div><h4>사전조건</h4><ul>{pre or "<li class=muted>없음</li>"}</ul>{f'<h4 style="margin-top:8px">테스트 데이터</h4><div>{e(c.get("data"))}</div>' if c.get("data") else ""}</div>
    <div><h4>절차</h4><ol>{steps}</ol></div>
    <div style="grid-column:1/-1"><h4>기대결과</h4><ul>{exp}</ul></div>
  </div>
  <div class="tc-meta"><span>요구사항 {e(", ".join(c["req_ids"]))}</span><span>근거 {e("; ".join(c.get("refs", [])) or "-")}</span><span>자동화 {e(c.get("automation", "-"))}</span>{f"<span>비고 {e(c['note'])}</span>" if c.get("note") else ""}</div>
</article>''')
    buttons = '<button type="button" aria-pressed="true" data-f="all">전체</button>' + "".join(f'<button type="button" aria-pressed="false" data-f="{p}">{p}</button>' for p in prios) + '<button type="button" aria-pressed="false" data-f="as">가정 의존</button>'
    notes = "".join(f"<li>{e(n)}</li>" for n in d.get("notes", []))

    return f"""<title>{e(f["name"])} 신규 TC</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap">
<style>
{CSS}
footer {{ font-size: .8rem; color: var(--muted); border-top: 1px solid var(--line); padding-top: 14px; }}
</style>
<div class="wrap">
  <header class="top">
    <div class="eyebrow">QA 오케스트라 · 신규 TC 작성가 산출물</div>
    <h1>{e(f["name"])} · 신규 기능 테스트 케이스</h1>
    <div class="meta"><span>작성일 {e(f.get("date", ""))}</span><span>기준: {e(f.get("analysis", ""))}</span></div>
  </header>
  <div class="kpis">
    <div class="kpi"><b>{len(cases)}</b><span>테스트 케이스</span></div>
    <div class="kpi{" warn" if pct < 100 else ""}"><b>{pct}%</b><span>요구사항 커버리지 ({covered}/{len(reqs)})</span></div>
    <div class="kpi warn"><b>{p0}</b><span>P0 (출시 차단)</span></div>
    <div class="kpi caution"><b>{len(as_cases)}</b><span>가정에 기댄 TC</span></div>
    <div class="kpi"><b>{auto}</b><span>자동화 후보</span></div>
  </div>
  <nav class="toc" aria-label="목차"><a href="#cov">요구사항 커버리지</a><a href="#dist">우선순위 × 유형</a><a href="#assume">가정에 기댄 TC</a><a href="#list">TC 목록</a></nav>
  <section id="cov"><h2><span class="no">1</span>요구사항 커버리지</h2>
    <div class="card tbl"><table class="cov"><thead><tr><th>요구사항</th><th>내용</th><th class="c">TC</th><th>케이스</th></tr></thead><tbody>{"".join(cov)}</tbody></table></div></section>
  <section id="dist"><h2><span class="no">2</span>우선순위 × 유형</h2>
    <div class="card tbl"><table class="dist">{dist}</table></div></section>
  <section id="assume"><h2><span class="no">3</span>가정에 기댄 TC <span class="muted" style="font-size:.8rem; font-weight:500">→ 답이 오면 이 TC부터 다시 본다</span></h2>
    <div class="card tbl">{f'<table><thead><tr><th>질문</th><th>내용 · 임시 가정</th><th>물어볼 사람</th><th>영향받는 TC</th></tr></thead><tbody>{as_rows}</tbody></table>' if as_rows else '<p class="muted">가정에 기댄 TC가 없어요.</p>'}</div></section>
  <section id="list"><h2><span class="no">4</span>TC 목록</h2>
    <div class="filters" role="group" aria-label="TC 거르기">{buttons}</div>
    <div class="card">{"".join(tcs)}</div>
    {f'<div class="card"><h3>메모</h3><ul>{notes}</ul></div>' if notes else ""}</section>
  <footer>QA 오케스트라 신규 TC 작성가가 만든 문서예요. 원본 데이터: test-cases.json · 엑셀용: test-cases.csv</footer>
</div>
<script>
document.querySelectorAll('.filters button').forEach(b => b.addEventListener('click', () => {{
  document.querySelectorAll('.filters button').forEach(x => x.setAttribute('aria-pressed', x === b));
  const f = b.dataset.f;
  document.querySelectorAll('.tc').forEach(t => {{
    t.hidden = !(f === 'all' || (f === 'as' ? t.dataset.a === '1' : t.dataset.p === f));
  }});
}}));
</script>
"""


def render_md(d, cfg, analysis):
    f = d["feature"]
    reqs, by_req, excused = coverage_rows(d, analysis)
    L = [f'# {f["name"]} · 신규 기능 테스트 케이스', "", f'작성일 {f.get("date", "")} · 기준: {f.get("analysis", "")}', "",
         "## 요구사항 커버리지", "| 요구사항 | 키워드 | TC |", "|---|---|---|"]
    for rid, kw, _ in reqs:
        cs = by_req.get(rid, [])
        L.append(f'| {rid} | {kw} | {", ".join(c["id"] for c in cs) or "없음 — " + excused.get(rid, "")} |')
    L += ["", "## TC 목록"]
    for c in d["cases"]:
        L += ["", f'### {c["id"]} {c["title"]}',
              f'- 영역: {c["group"]} · 유형: {c["type"]} · 우선순위: {c["priority"]} · 요구사항: {", ".join(c["req_ids"])}']
        if c.get("assumptions"):
            L.append(f'- 가정: {", ".join(c["assumptions"])}')
        if c.get("preconditions"):
            L.append("- 사전조건: " + "; ".join(c["preconditions"]))
        if c.get("data"):
            L.append(f'- 테스트 데이터: {c["data"]}')
        L.append("- 절차:")
        L += [f"  {i}. {s}" for i, s in enumerate(c["steps"], 1)]
        L.append("- 기대결과:")
        L += [f"  - {x}" for x in c["expected"]]
    if d.get("notes"):
        L += ["", "## 메모"] + [f"- {n}" for n in d["notes"]]
    return "\n".join(L) + "\n"


def main():
    args = sys.argv[1:]
    opt = {}
    for k in ("--analysis", "--config"):
        if k in args:
            i = args.index(k)
            opt[k] = args[i + 1]
            del args[i:i + 2]
    if not args:
        sys.exit(__doc__)
    src = Path(args[0])
    out = Path(args[1]) if len(args) > 1 else src.parent
    d = json.loads(src.read_text(encoding="utf-8"))
    cfg = load_config(opt.get("--config"))
    analysis = json.loads(Path(opt["--analysis"]).read_text(encoding="utf-8")) if opt.get("--analysis") else None
    errs = check(d, cfg, analysis)
    if errs:
        print("✘ 점검 실패 — test-cases.json을 고쳐 다시 돌리세요:")
        print("\n".join(f"  - {x}" for x in errs))
        sys.exit(1)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(d, cfg, out / "test-cases.csv")
    (out / "test-cases.html").write_text(render_html(d, cfg, analysis), encoding="utf-8")
    (out / "test-cases.md").write_text(render_md(d, cfg, analysis), encoding="utf-8")
    print(f"✔ TC {len(d['cases'])}개 점검 통과")
    for n in ("csv", "html", "md"):
        print(f"✔ {out / ('test-cases.' + n)}")


if __name__ == "__main__":
    main()
