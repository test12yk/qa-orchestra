#!/usr/bin/env python3
"""기획 분석가가 쓴 plan-analysis.json을 확정 양식의 HTML 보고서와 마크다운으로 그린다.

사용법: python3 tools/render_plan_analysis.py <plan-analysis.json> [출력 폴더]
출력: <출력 폴더>/plan-analysis.html, <출력 폴더>/plan-analysis.md
"""
import html
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CSS = (HERE / "templates" / "plan_analysis.css").read_text(encoding="utf-8")

SOURCE_ORDER = ["plan", "policy", "dev", "design", "slack"]
SOURCE_LABEL = {"plan": "기획서", "policy": "정책", "dev": "개발", "design": "디자인", "slack": "슬랙"}
LEVEL_CLASS = {"red": "red", "amber": "amber", "green": "green"}
RISK_LABEL = {"high": "상", "mid": "중", "low": "하"}
RISK_FLAG = {"high": "red", "mid": "amber", "low": "green"}
IMPACT_LABEL = {"direct": ("직접", "red"), "indirect": ("간접", "amber")}

e = lambda s: html.escape(str(s if s is not None else ""))


def short_id(rid):
    return rid.split("-")[-1]


def flag(level, label):
    return f'<span class="flag {LEVEL_CLASS.get(level, "amber")}">{e(label)}</span>'


def src_dots(req):
    dots = []
    for s in SOURCE_ORDER:
        st = req.get("sources", {}).get(s, "none")
        dots.append(f'<i class="dot {"s-" + s if st != "none" else "off"}" title="{SOURCE_LABEL[s]}"></i>')
    return '<span class="srcs">' + "".join(dots) + "</span>"


# ── 벤다이어그램: 기획서·정책·개발 3원. 칩이 자기 영역 밖으로 나가지 않는 자리를 찾아 배치 ──
VENN = {"plan": (245, 220), "policy": (435, 220), "dev": (340, 375)}
VENN_R = 180  # 한 영역에 칩 3개까지 들어가는 크기
CHIP_W, CHIP_H, CHIP_GAP = 100, 22, 4


def _membership(x, y):
    return tuple(int(math.hypot(x - cx, y - cy) < VENN_R) for cx, cy in VENN.values())


def _stack_fits(cx, y, n, want):
    total_h = n * CHIP_H + (n - 1) * CHIP_GAP
    for yy in (y, y + total_h / 2, y + total_h):
        for x in (cx - CHIP_W / 2, cx, cx + CHIP_W / 2):
            if _membership(x, yy) != want:
                return False
    return True


def _place(want, n):
    """영역 want에 n개 칩을 세로로 쌓을 수 있는 자리 중 영역 한가운데에 가까운 곳."""
    while n > 0:
        spots = [(cx, y) for cx in range(40, 641, 4) for y in range(20, 580, 4) if _stack_fits(cx, y, n, want)]
        if spots:
            mx = sum(p[0] for p in spots) / len(spots)
            my = sum(p[1] for p in spots) / len(spots)
            return min(spots, key=lambda p: (p[0] - mx) ** 2 + (p[1] - my) ** 2), n
        n -= 1
    return None, 0


def venn_svg(reqs):
    keys = list(VENN)
    groups, outside = {}, []
    for r in reqs:
        want = tuple(int(r.get("sources", {}).get(k, "none") != "none") for k in keys)
        if sum(want) == 0:
            outside.append(r)
        else:
            groups.setdefault(want, []).append(r)
    parts = [
        '<svg viewBox="0 0 680 600" role="img" aria-label="기획서, 정책, 개발 문서의 요구사항 벤다이어그램">',
        *[f'<circle class="c c-{k}" cx="{x}" cy="{y}" r="{VENN_R}"/>' for k, (x, y) in VENN.items()],
        '<text class="lbl" x="18" y="36" style="fill: var(--plan)">기획서</text>',
        '<text class="lbl" x="662" y="36" text-anchor="end" style="fill: var(--policy)">정책</text>',
        '<text class="lbl" x="340" y="592" text-anchor="middle" style="fill: var(--dev)">개발 문서</text>',
    ]
    overflow = []
    for want, items in groups.items():
        (pos, fit) = _place(want, len(items))
        if not pos:
            overflow.extend(items)
            continue
        cx, y = pos
        for i, r in enumerate(items[:fit]):
            yy = y + i * (CHIP_H + CHIP_GAP)
            warn = any(v == "conflict" for v in r.get("sources", {}).values())
            parts.append(
                f'<g class="chip{" warn" if warn else ""}"><title>{e(r["id"])} {e(r["text"])}</title>'
                f'<rect x="{cx - CHIP_W // 2}" y="{yy}" width="{CHIP_W}" height="{CHIP_H}" rx="4"/>'
                f'<text x="{cx}" y="{yy + 15}" text-anchor="middle"><tspan class="cid">{e(short_id(r["id"]))}</tspan> {e(r["keyword"])}</text></g>'
            )
        overflow.extend(items[fit:])
    parts.append("</svg>")
    return "\n".join(parts), outside, overflow


def tree_html(reqs, root_name):
    groups = {}
    for r in reqs:
        groups.setdefault(r.get("group", "기타"), []).append(r)
    out = [f'<ul class="tree"><li><span class="node-group">{e(root_name)}</span><ul>']
    for g, items in groups.items():
        out.append(f'<li><span class="node-group">{e(g)}</span><ul>')
        for r in items:
            flags = "".join(flag(f["level"], f["label"]) for f in r.get("flags", []))
            out.append(f'<li class="req"><span class="rid">{e(r["id"])}</span>{e(r["text"])} {src_dots(r)}{flags}</li>')
        out.append("</ul></li>")
    out.append("</ul></li></ul>")
    return "".join(out)


def matrix_html(reqs):
    mark = {"agree": '<td class="c yes">●</td>', "conflict": '<td class="c x">✕</td>', "none": '<td class="c no">·</td>'}
    rows = []
    for r in reqs:
        cells = "".join(mark.get(r.get("sources", {}).get(s, "none"), mark["none"]) for s in SOURCE_ORDER)
        rows.append(f'<tr><td><span class="rid">{e(short_id(r["id"]))}</span> {e(r["keyword"])}</td>{cells}</tr>')
    head = "".join(f'<th class="c">{SOURCE_LABEL[s]}</th>' for s in SOURCE_ORDER)
    return f'<div class="tbl"><table class="mx"><thead><tr><th>요구사항</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def conflicts_html(conflicts):
    rows = []
    for c in conflicts:
        cells = []
        for s in ["plan", "policy", "dev"]:
            v = c.get("by_source", {}).get(s)
            cells.append(f'<td>{e(v["text"])}<span class="quote">{e(v.get("ref", ""))}</span></td>' if v else '<td class="muted">–</td>')
        extra = [f'{"🎨" if s == "design" else "💬"} {e(c["by_source"][s]["text"])}' for s in ["design", "slack"] if c.get("by_source", {}).get(s)]
        cells.append(f'<td>{" · ".join(extra)}</td>' if extra else '<td class="muted">–</td>')
        v = c.get("verdict", {})
        rows.append(
            f'<tr><td class="mono">{e(c["id"])}</td><td><b>{e(c["topic"])}</b><span class="quote">{e(" · ".join(c.get("req_ids", [])))}</span></td>'
            f'{"".join(cells)}<td class="verdict">{flag(v.get("level", "red"), v.get("label", "충돌"))}</td></tr>'
        )
    return ('<div class="card tbl"><table><thead><tr><th>#</th><th>항목</th><th>기획서</th><th>정책</th><th>개발</th><th>디자인 · 슬랙</th><th>판단</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def impact_html(impacts, root):
    out = [f'<ul class="tree impact"><li><span class="node-group">{e(root)}</span><ul>']
    for g in impacts:
        out.append(f'<li><span class="node-group">{e(g["group"])}</span><ul>')
        for it in g["items"]:
            lab, lv = IMPACT_LABEL.get(it.get("level", "indirect"), IMPACT_LABEL["indirect"])
            out.append(f'<li>{e(it["name"])} <span class="flag {lv} tag">{lab}</span></li>')
        out.append("</ul></li>")
    out.append("</ul></li></ul>")
    return "".join(out)


def risk_grid(risks):
    cell_level = {("high", "high"): "hi", ("high", "mid"): "hi", ("mid", "high"): "md", ("high", "low"): "md",
                  ("mid", "mid"): "md", ("low", "high"): "lo", ("mid", "low"): "lo", ("low", "mid"): "lo", ("low", "low"): "lo"}
    out = ['<div class="rgrid">']
    for imp in ["high", "mid", "low"]:
        out.append(f'<div class="ax">{"영향<br>" if imp == "high" else ""}{RISK_LABEL[imp]}</div>')
        for lik in ["low", "mid", "high"]:
            items = "".join(f'<span class="it">{e(r["area"])}</span>' for r in risks if r["impact"] == imp and r["likelihood"] == lik)
            out.append(f'<div class="cell {cell_level[(imp, lik)]}">{items}</div>')
    out.append('<div></div><div class="axis-x" style="grid-column: 2 / 3">가능성 하</div>'
               '<div class="axis-x" style="grid-column: 3 / 4">중</div><div class="axis-x" style="grid-column: 4 / 5">상</div></div>')
    return "".join(out)


def risk_table(risks):
    rows = "".join(f'<tr><td>{e(r["area"])}</td><td>{flag(RISK_FLAG[r["level"]], RISK_LABEL[r["level"]])}</td>'
                   f'<td>{e(r["reason"])}</td><td class="mono">{e(r["priority"])}</td></tr>' for r in risks)
    return f'<div class="card tbl"><table><thead><tr><th>영역</th><th>위험</th><th>근거</th><th>TC 우선순위</th></tr></thead><tbody>{rows}</tbody></table></div>'


def questions_html(qs):
    rows = "".join(f'<tr><td class="mono">{e(q["id"])}</td><td>{e(q["question"])}</td><td>{e(q.get("ask_to", ""))}</td>'
                   f'<td>{e(q.get("why", ""))}</td><td>{e(q.get("assumption", ""))}</td><td class="mono">{e(" · ".join(q.get("refs", [])))}</td></tr>' for q in qs)
    return ('<div class="card tbl"><table><thead><tr><th>#</th><th>질문</th><th>물어볼 사람</th><th>왜 중요한가</th><th>답 오기 전 임시 가정</th><th>관련</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


def render_html(d):
    f = d["feature"]
    reqs, conflicts, risks, qs = d["requirements"], d.get("conflicts", []), d.get("risks", []), d.get("questions", [])
    venn, outside, overflow = venn_svg(reqs)
    # 기획서·정책·개발 어디에도 없는 요구사항 = 디자인이나 슬랙에서만 정해진 것
    no_doc = sum(1 for r in reqs if all(r.get("sources", {}).get(s, "none") == "none" for s in ["plan", "policy", "dev"]))
    high = sum(1 for r in risks if r["level"] == "high")
    docs = "".join(f'<span class="doc"><i class="dot s-{s["type"]}"></i>{e(s["title"])} · {e(s.get("date", ""))}</span>' for s in d["sources"])
    outside_html = "".join(f'<div><i class="dot s-{next((s for s in SOURCE_ORDER if r["sources"].get(s, "none") != "none"), "slack")}"></i> '
                           f'{e(short_id(r["id"]))} {e(r["keyword"])} — {e(r["text"])}</div>' for r in outside)
    overflow_html = ("<div><b>그림에 다 못 넣은 항목</b></div>" + "".join(f'<div>{e(short_id(r["id"]))} {e(r["keyword"])}</div>' for r in overflow)) if overflow else ""
    sm = f.get("summary", {})
    scope = d.get("scope", {})
    scope_in = "".join(f"<li>{e(x)}</li>" for x in scope.get("in", []))
    scope_out = "".join(f'<li>{e(x["item"])} — {e(x.get("reason", ""))}</li>' for x in scope.get("out", []))
    prep = "".join(f'<div><h3>{e(p["title"])}</h3><ul>{"".join(f"<li>{e(i)}</li>" for i in p["items"])}</ul></div>' for p in d.get("prep", []))
    return f"""<title>{e(f["name"])} 기획 분석서</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap">
<style>
{CSS}</style>
<div class="wrap">
  <header class="top">
    <div class="eyebrow">QA 오케스트라 · 기획 분석가 산출물</div>
    <h1>{e(f["name"])} · 기획 분석서</h1>
    <div class="meta"><span>분석일 {e(f.get("date", ""))}</span><span>{e(f.get("sprint", ""))}</span></div>
    <div class="docs" aria-label="입력 문서">{docs}</div>
  </header>
  <div class="kpis">
    <div class="kpi"><b>{len(reqs)}</b><span>요구사항</span></div>
    <div class="kpi warn"><b>{len(conflicts)}</b><span>문서 간 충돌</span></div>
    <div class="kpi caution"><b>{no_doc}</b><span>문서에 없는 결정·규칙</span></div>
    <div class="kpi"><b>{len(qs)}</b><span>확인 질문</span></div>
    <div class="kpi warn"><b>{high}</b><span>고위험 영역</span></div>
  </div>
  <nav class="toc" aria-label="목차"><a href="#summary">기능 요약</a><a href="#tree">요구사항 트리</a><a href="#coverage">문서 커버리지</a><a href="#conflict">문서 간 충돌</a><a href="#impact">영향 범위</a><a href="#risk">위험 분석</a><a href="#questions">확인 질문</a><a href="#scope">범위·준비</a></nav>
  <section id="summary"><h2><span class="no">1</span>기능 요약</h2>
    <div class="card summary"><dl><dt>목적</dt><dd>{e(sm.get("purpose"))}</dd><dt>대상</dt><dd>{e(sm.get("target"))}</dd><dt>핵심 흐름</dt><dd>{e(sm.get("flow"))}</dd><dt>QA 핵심</dt><dd>{e(sm.get("qa_focus"))}</dd></dl></div></section>
  <section id="tree"><h2><span class="no">2</span>요구사항 트리</h2>
    <div class="legend">{"".join(f'<span><i class="dot s-{s}"></i>{SOURCE_LABEL[s]}</span>' for s in SOURCE_ORDER)}<span>· 네모 순서 = 기획/정책/개발/디자인/슬랙, 빈 네모 = 언급 없음</span></div>
    <div class="card">{tree_html(reqs, f["name"])}</div></section>
  <section id="coverage"><h2><span class="no">3</span>문서 커버리지</h2><div class="two">
    <div class="card venn"><h3>주요 문서 3종에서 요구사항이 어디에 적혀 있나</h3>{venn}
      <div class="outside">{"<div><b>세 문서 밖에만 있는 것</b></div>" + outside_html if outside else ""}{overflow_html}
      <p class="muted" style="font-size:.82rem">숫자는 요구사항 번호(REQ-xx), 항목에 마우스를 올리면 전체 내용이 보여요. 빨간 테두리 = 문서 간 충돌, 한 문서에만 있는 항목은 확인 대상이에요.</p></div></div>
    <div class="card"><h3>문서별 언급 매트릭스</h3>{matrix_html(reqs)}<p class="muted" style="font-size:.8rem; margin-top:8px">● 언급하고 서로 일치 · ✕ 언급했지만 다른 문서와 내용이 다름 · 언급 없음</p></div>
  </div></section>
  <section id="conflict"><h2><span class="no">4</span>문서 간 충돌</h2>{conflicts_html(conflicts)}</section>
  <section id="impact"><h2><span class="no">5</span>영향받는 기존 기능 <span class="muted" style="font-size:.8rem; font-weight:500">→ 회귀 TC 관리자에게 전달</span></h2><div class="card">{impact_html(d.get("impacts", []), f["name"])}</div></section>
  <section id="risk"><h2><span class="no">6</span>위험 분석</h2><div class="two"><div class="card"><h3 style="margin-bottom:10px">위험 매트릭스 (영향 × 발생 가능성)</h3>{risk_grid(risks)}</div>{risk_table(risks)}</div></section>
  <section id="questions"><h2><span class="no">7</span>확인 질문</h2>{questions_html(qs)}</section>
  <section id="scope"><h2><span class="no">8</span>테스트 범위 · 준비 사항</h2>
    <div class="scope"><div class="card"><h3>이번에 테스트하는 것</h3><ul>{scope_in}</ul></div><div class="card out"><h3>이번에 테스트하지 않는 것</h3><ul>{scope_out}</ul></div></div>
    <div class="card prep">{prep}</div></section>
  <footer>QA 오케스트라 기획 분석가가 만든 문서예요. 원본 데이터: plan-analysis.json</footer>
</div>
"""


def render_md(d):
    f = d["feature"]
    L = [f'# {f["name"]} · 기획 분석서', "", f'분석일 {f.get("date", "")} · {f.get("sprint", "")}', "", "## 입력 문서"]
    L += [f'- [{SOURCE_LABEL.get(s["type"], s["type"])}] {s["title"]} ({s.get("date", "")}) `{s.get("path", "")}`' for s in d["sources"]]
    sm = f.get("summary", {})
    L += ["", "## 1. 기능 요약", f'- 목적: {sm.get("purpose", "")}', f'- 대상: {sm.get("target", "")}', f'- 핵심 흐름: {sm.get("flow", "")}', f'- QA 핵심: {sm.get("qa_focus", "")}']
    L += ["", "## 2. 요구사항", "| ID | 그룹 | 키워드 | 내용 | 출처(기/정/개/디/슬) | 근거 | 표시 |", "|---|---|---|---|---|---|---|"]
    sym = {"agree": "●", "conflict": "✕", "none": "·"}
    for r in d["requirements"]:
        srcs = "".join(sym.get(r.get("sources", {}).get(s, "none"), "·") for s in SOURCE_ORDER)
        L.append(f'| {r["id"]} | {r.get("group", "")} | {r["keyword"]} | {r["text"]} | {srcs} | {"; ".join(r.get("refs", []))} | {", ".join(x["label"] for x in r.get("flags", []))} |')
    L += ["", "## 3. 문서 간 충돌"]
    for c in d.get("conflicts", []):
        L.append(f'### {c["id"]} {c["topic"]} ({", ".join(c.get("req_ids", []))}) → {c.get("verdict", {}).get("label", "")}')
        L += [f'- {SOURCE_LABEL[s]}: {v["text"]} ({v.get("ref", "")})' for s, v in c.get("by_source", {}).items()]
    L += ["", "## 4. 영향받는 기존 기능 (회귀 TC 관리자 전달)"]
    for g in d.get("impacts", []):
        L.append(f'- {g["group"]}')
        L += [f'  - {it["name"]} [{IMPACT_LABEL.get(it.get("level"), ("간접",))[0]}]' for it in g["items"]]
    L += ["", "## 5. 위험 분석", "| 영역 | 영향 | 가능성 | 위험 | 근거 | 우선순위 |", "|---|---|---|---|---|---|"]
    L += [f'| {r["area"]} | {RISK_LABEL[r["impact"]]} | {RISK_LABEL[r["likelihood"]]} | {RISK_LABEL[r["level"]]} | {r["reason"]} | {r["priority"]} |' for r in d.get("risks", [])]
    L += ["", "## 6. 확인 질문", "| # | 질문 | 물어볼 사람 | 왜 중요한가 | 임시 가정 | 관련 |", "|---|---|---|---|---|---|"]
    L += [f'| {q["id"]} | {q["question"]} | {q.get("ask_to", "")} | {q.get("why", "")} | {q.get("assumption", "")} | {", ".join(q.get("refs", []))} |' for q in d.get("questions", [])]
    sc = d.get("scope", {})
    L += ["", "## 7. 테스트 범위", "**포함**"] + [f"- {x}" for x in sc.get("in", [])] + ["", "**제외**"] + [f'- {x["item"]} — {x.get("reason", "")}' for x in sc.get("out", [])]
    L += ["", "## 8. 준비 사항"]
    for p in d.get("prep", []):
        L.append(f'- **{p["title"]}**: ' + "; ".join(p["items"]))
    return "\n".join(L) + "\n"


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else src.parent
    d = json.loads(src.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan-analysis.html").write_text(render_html(d), encoding="utf-8")
    (out / "plan-analysis.md").write_text(render_md(d), encoding="utf-8")
    print(f"✔ {out / 'plan-analysis.html'}\n✔ {out / 'plan-analysis.md'}")


if __name__ == "__main__":
    main()
