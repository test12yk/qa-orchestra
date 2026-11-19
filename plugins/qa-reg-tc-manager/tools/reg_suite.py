#!/usr/bin/env python3
"""회귀 TC 관리자 도구: 회귀 스위트를 읽고 쓰고(import·render), 신규 기능 TC의 편입 후보를 기준표로 매기고
(candidates·render-candidates), 사람이 승인한 것만 스위트에 넣고(apply), 릴리즈 회귀 범위를 고른다(select·render-selection).

사용법:
  python3 reg_suite.py import <기존 스위트 xlsx|csv> --out <suite.json> [--name "<스위트 이름>"]
  python3 reg_suite.py render <suite.json> [출력 폴더]
      → regression-suite.xlsx(영역별 탭 + 변경 이력 탭), suite.html, suite.md
  python3 reg_suite.py candidates --suite <suite.json> --tc <test-cases.json> [--review <실행 결과 review.json>]
                                  [--judgment <judgment.json>] [--analysis <plan-analysis.json>] --out <폴더>
      → <폴더>/candidates-facts.json  (TC마다 기준 점수·추천 후보·겹치는 기존 케이스, 영향받는 기존 케이스)
  python3 reg_suite.py render-candidates <candidates.json> [출력 폴더]
      → candidates.html, candidates.md  (candidates.json = facts + 관리자가 쓴 recommendation·reason, updates)
  python3 reg_suite.py apply <candidates.json> --suite <suite.json> --approve RC-01,RC-02 [--approve-updates U-01]
                             --by "<승인한 사람>" [--note "<이유>"] [--before-decision]
      → 승인한 것만 스위트에 넣고(새 RG 번호) 다시 그린다. 변경 이력에 남는다.
  python3 reg_suite.py select --suite <suite.json> --features <judgment.json> ... [--ledger <features.json>]
                              [--analysis <plan-analysis.json> ...] --release "<릴리즈 이름>" --out <폴더>
      → <폴더>/selection-facts.json  (케이스마다 필수·직접 영향·간접 영향·제외와 이유)
  python3 reg_suite.py render-selection <selection.json> [출력 폴더]
      → regression-run.xlsx(이번에 돌릴 케이스만, 결과·이슈내용 칸), selection.html, selection.md

공통 옵션: --config <qa-orchestra.config.json>
"""
import argparse
import csv
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

REC = {"include": ("넣기", "green"), "exclude": ("빼기", "muted"), "review": ("검토", "amber"), "defer": ("나중에", "amber")}
CAT = {"must": ("필수", "red"), "direct": ("직접 영향", "amber"), "indirect": ("간접 영향", "amber"), "skip": ("이번엔 제외", "muted")}
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


# ── 셀 ↔ 케이스 ──
LIST_KEYS = ("preconditions", "steps", "expected")


def to_cell(c, key, cfg):
    s = cfg["suite"]
    v = c.get(key, "")
    if key == "steps":
        return "\n".join(f"{i}. {x}" for i, x in enumerate(v or [], 1))
    if key in ("preconditions", "expected"):
        return "\n".join(f"- {x}" for x in v or [])
    if key == "automation":
        return s["automation"].get(v, v or "")
    if key == "result":
        return s["results"].get(v, v or "")
    return ", ".join(map(str, v)) if isinstance(v, list) else str(v or "")


def from_cell(key, v, cfg):
    s = cfg["suite"]
    v = (v or "").strip()
    if key in LIST_KEYS:
        return [re.sub(r"^\s*(?:-|\d+[.)])\s*", "", x).strip() for x in v.splitlines() if x.strip()]
    if key == "automation":
        return next((k for k, lab in s["automation"].items() if lab == v or k == v.lower()), v)
    if key == "result":
        return next((k for k, lab in s["results"].items() if lab.lower() == v.lower()), "")
    return v


def cmd_import(args, cfg):
    cols = cfg["suite"]["columns"]
    by_label = {c["label"].strip().lower(): c["key"] for c in cols}
    p = Path(args.source)
    if p.suffix.lower() == ".csv":
        with p.open(encoding="utf-8-sig") as f:
            tabs = [(p.stem, list(csv.reader(f)))]
    else:
        tabs = xlsx.read(p)
    cases, seen = [], set()
    for name, rows in tabs:
        if name == cfg["suite"]["history_tab"] or not rows:
            continue
        keys = [by_label.get(h.strip().lower()) for h in rows[0]]
        if "id" not in keys or "title" not in keys:
            print(f"건너뜀: '{name}' 탭 — CaseID·title 머리글이 없음")
            continue
        for r in rows[1:]:
            c = {k: from_cell(k, r[i] if i < len(r) else "", cfg) for i, k in enumerate(keys) if k}
            if not c.get("id") or not c.get("title"):
                continue
            if c["id"] in seen:
                sys.exit(f"CaseID 중복: {c['id']}")
            seen.add(c["id"])
            if c.get("result"):
                c["last_result"] = c.pop("result")
            c.pop("issue", None)
            cases.append({k: v for k, v in c.items() if v not in ("", [], None)})
    suite = {"name": args.name or p.stem, "version": 1, "updated_at": now(), "cases": cases,
             "history": [{"at": now(), "version": 1, "change": "import", "ids": [c["id"] for c in cases], "note": f"{p.name}에서 가져옴"}]}
    write_json(args.out, suite)
    print(f"스위트 가져옴: {len(cases)}개 → {args.out}")


# ── 스위트 그리기 ──
def tabs_of(cases, cfg):
    key = cfg["suite"]["tab_by"]
    out = {}
    for c in cases:
        out.setdefault(c.get(key) or "기타", []).append(c)
    return out


def suite_sheets(cases, cfg, history=None, results=False):
    s = cfg["suite"]
    cols = [c for c in s["columns"] if results or c["key"] not in ("result", "issue")]
    lists = {i: v for i, v in ((next((i for i, c in enumerate(cols) if c["key"] == k), None), vals) for k, vals in
                              (("type", s["types"]), ("priority", s["priorities"]), ("result", list(s["results"].values())),
                               ("automation", list(s["automation"].values())))) if i is not None}
    sheets = [{"name": tab, "header": [c["label"] for c in cols], "widths": [c.get("width", 16) for c in cols], "lists": lists,
               "rows": [[to_cell(c, col["key"], cfg) for col in cols] for c in cs]} for tab, cs in tabs_of(cases, cfg).items()]
    if history:
        sheets.append({"name": s["history_tab"], "header": ["버전", "날짜", "변경", "케이스", "누가", "출처·이유"], "widths": [6, 18, 8, 40, 16, 50],
                       "rows": [[h.get("version", ""), h["at"][:16].replace("T", " "), h["change"], ", ".join(h.get("ids", [])), h.get("by", ""),
                                 " · ".join(x for x in (h.get("from", ""), h.get("note", "")) if x)] for h in history]})
    return sheets


def page(title, eyebrow, body, meta=""):
    css = (HERE / "templates" / "reg.css").read_text(encoding="utf-8")
    return (f'<!doctype html>\n<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{e(title)}</title><style>{css}</style></head>\n<body><div class=\"wrap\">"
            f'<header class="top"><span class="eyebrow">QA 오케스트라 · {e(eyebrow)}</span><h1>{e(title)}</h1><div class="meta">{meta}</div></header>'
            f"{body}</div></body></html>\n")


def counts(cases, key):
    out = {}
    for c in cases:
        out[c.get(key) or "-"] = out.get(c.get(key) or "-", 0) + 1
    return out


def case_rows(cases, cfg, extra=None):
    al = cfg["suite"]["automation"]
    return "".join(f'<tr><td class="mono">{e(c["id"])}</td><td>{e(c.get("section", ""))}</td><td>{e(c["title"])}'
                   + (f'<div class="muted small">{e(extra(c))}</div>' if extra and extra(c) else "")
                   + f'</td><td class="c">{e(c.get("priority", ""))}</td><td class="c">{e(al.get(c.get("automation"), c.get("automation", "")))}</td></tr>' for c in cases)


def cmd_render(args, cfg):
    st = read_json(args.suite)
    out = Path(args.outdir or Path(args.suite).parent)
    out.mkdir(parents=True, exist_ok=True)
    cases = [c for c in st["cases"] if not c.get("retired")]
    xlsx.write(out / "regression-suite.xlsx", suite_sheets(cases, cfg, st.get("history")), title=st["name"])
    tabs = tabs_of(cases, cfg)
    pri, auto = counts(cases, "priority"), counts(cases, "automation")
    al = cfg["suite"]["automation"]
    kpi = ('<div class="kpis">' + f'<div class="kpi"><b>{len(cases)}</b><span>케이스</span></div><div class="kpi"><b>{len(tabs)}</b><span>영역(탭)</span></div>'
           + "".join(f'<div class="kpi"><b>{v}</b><span>{e(k)}</span></div>' for k, v in sorted(pri.items()))
           + "".join(f'<div class="kpi"><b>{v}</b><span>{e(al.get(k, k))}</span></div>' for k, v in auto.items()) + "</div>")
    secs = "".join(f'<section><h2>{e(t)} <span class="muted small">{len(cs)}개</span></h2><div class="tbl"><table><thead><tr><th>CaseID</th><th>section</th><th>title</th>'
                   f'<th class="c">우선</th><th class="c">자동화</th></tr></thead><tbody>{case_rows(cs, cfg, lambda c: c.get("source", ""))}</tbody></table></div></section>'
                   for t, cs in tabs.items())
    hist = "".join(f'<li>v{h.get("version", "")} · {e(h["at"][:16].replace("T", " "))} · {e(h["change"])} {len(h.get("ids", []))}개'
                   f'{" · " + e(h["by"]) if h.get("by") else ""}{" — " + e(h.get("from", "")) if h.get("from") else ""}{" · " + e(h["note"]) if h.get("note") else ""}</li>'
                   for h in reversed(st.get("history", [])))
    body = kpi + secs + f'<section><h2>변경 이력</h2><ul class="ev">{hist}</ul></section>'
    (out / "suite.html").write_text(page(f'{st["name"]}', "회귀 TC 관리자 · 회귀 스위트", body,
                                         f'<span>v{st["version"]}</span><span>{e(st["updated_at"][:16].replace("T", " "))}</span>'), encoding="utf-8")
    md = [f"# {st['name']} (v{st['version']})", "", f"케이스 {len(cases)}개 · 영역 {len(tabs)}개 · " + " · ".join(f"{k} {v}" for k, v in sorted(pri.items()))]
    for t, cs in tabs.items():
        md += ["", f"## {t} ({len(cs)}개)", "", "| CaseID | section | title | 우선 |", "|---|---|---|---|"]
        md += [f"| {c['id']} | {c.get('section', '')} | {c['title']} | {c.get('priority', '')} |" for c in cs]
    (out / "suite.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"스위트 v{st['version']}: {len(cases)}개 · 탭 {', '.join(f'{t} {len(cs)}' for t, cs in tabs.items())} → {out / 'regression-suite.xlsx'}")


# ── 편입 후보 ──
def norm(s):
    return re.sub(r"\([^)]*\)|[\s·,/()\-]", "", str(s or ""))


def similar(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def impact_names(judgment, analyses):
    """영향받는 기존 기능 이름과 정도(direct/indirect). 분석서가 있으면 그 정도를, 없으면 판정서(직접 영향)를 쓴다."""
    out = {}
    for an in analyses:
        for g in an.get("impacts", []):
            for it in g.get("items", []):
                out[it["name"]] = it.get("level", "direct")
    for im in (judgment or {}).get("impacts", []):
        out.setdefault(im["name"], "direct")
    return out


def matches(case, name):
    core = norm(name)
    sec = norm(case.get("section", ""))
    return bool(sec) and (sec in core or core in sec or all(t in name for t in re.findall(r"\w{2,}", case.get("section", ""))))


def cmd_candidates(args, cfg):
    rc = cfg["regression"]
    st = read_json(args.suite)
    tc = read_json(args.tc)
    rv = read_json(args.review) if args.review else {}
    jd = read_json(args.judgment) if args.judgment else {}
    ans = [read_json(a) for a in args.analysis]
    res = {r["id"]: r for r in rv.get("tc_results", [])}
    assumed = {}
    for ck in rv.get("checks", []):
        if ck.get("code") == "PASS_ON_ASSUMPTION":
            for t in ck["targets"][1:]:
                assumed.setdefault(t, []).append(ck["targets"][0])
    for c in tc["cases"]:
        for q in c.get("assumptions") or []:
            if q not in assumed.get(c["id"], []):
                assumed.setdefault(c["id"], []).append(q)
    w = rc["weights"]
    imp = impact_names(jd, ans)
    live = [c for c in st["cases"] if not c.get("retired")]
    feature = tc.get("feature", {}).get("name") if isinstance(tc.get("feature"), dict) else tc.get("feature", "")
    items = []
    for c in tc["cases"]:
        r = res.get(c["id"], {})
        text = " ".join([c.get("domain", ""), c.get("section", ""), c["title"]])
        crit = {
            "core": c.get("priority") in rc["core_priorities"] or any(k in text for k in rc["core_keywords"]),
            "business": c.get("priority") in rc["business_priorities"],
            "change": c.get("type") == "리그레이션" or any(matches(c, n) for n, lv in imp.items() if lv == "direct"),
            "automatable": r.get("mode") == "api",
        }
        score = sum(w[k] for k, v in crit.items() if v)
        result = r.get("result", "")
        # 겹침: 같은 Domain에서 제목이 비슷하거나, 리그레이션 TC가 같은 section의 기존 케이스를 다시 확인하는 경우
        dups = []
        for s in live:
            if s.get("domain") != c.get("domain"):
                continue
            ratio = round(similar(s["title"], c["title"]), 2)
            same_sec = c.get("type") == "리그레이션" and norm(s.get("section")) == norm(c.get("section"))
            if ratio >= rc["duplicate_ratio"] or (same_sec and ratio >= rc["duplicate_ratio_same_section"]):
                dups.append({"id": s["id"], "title": s["title"], "ratio": ratio})
        dups = sorted(dups, key=lambda x: -x["ratio"])[:2]
        qs = sorted(set(assumed.get(c["id"], [])), key=lambda q: int(q[1:]) if q[1:].isdigit() else 0)
        if result != "pass" or qs:
            hint = "defer"
        elif dups:
            hint = "review"
        else:
            hint = "include" if score >= rc["include_at"] else "exclude" if score <= rc["exclude_at"] else "review"
        items.append({"id": f"RC-{len(items) + 1:02d}", "tc": {k: c.get(k) for k in ("id", "domain", "section", "type", "title", "priority")},
                      "result": result, "mode": r.get("mode", ""), "bugs": [b for b in r.get("bugs", []) if b],
                      "criteria": crit, "score": score, "hint": hint, "duplicates": dups,
                      **({"assumed": qs, "note": f"{', '.join(qs)} 답 전 — 임시 가정 기준 결과"} if qs else {})})
    # 영역(section)마다 대표 케이스만 넣기 후보로 둔다: 기본기능 → 최상위 우선순위 → 점수 순. 나머지는 검토.
    limit = rc.get("per_section_limit")
    if limit:
        groups = {}
        for it in items:
            if it["hint"] == "include":
                groups.setdefault((it["tc"]["domain"], it["tc"]["section"]), []).append(it)
        rank = {t: i for i, t in enumerate(cfg["suite"]["types"])}
        for (dom, sec), its in groups.items():
            its.sort(key=lambda it: (rank.get(it["tc"]["type"], 9), it["tc"]["priority"], -it["score"], it["id"]))
            for it in its[limit:]:
                it["hint"] = "review"
                it["note"] = f"'{sec}'에 넣기 후보가 {len(its)}개 — 대표 {limit}개({', '.join(x['tc']['id'] for x in its[:limit])}) 밖이라 검토"
    # 기능 하나가 스위트에 넣을 수 있는 수: 점수 → 최상위 우선순위 → 유형 순으로 한도 안만 넣기 후보
    cap = rc.get("max_per_feature")
    if cap:
        rank = {t: i for i, t in enumerate(cfg["suite"]["types"])}
        inc = sorted((it for it in items if it["hint"] == "include"),
                     key=lambda it: (-it["score"], it["tc"]["priority"], rank.get(it["tc"]["type"], 9), it["id"]))
        for it in inc[cap:]:
            it["hint"] = "review"
            it["note"] = f"기능당 넣기 최대 {cap}개 — 순위 {inc.index(it) + 1}위라 한도 밖"
    affected = [{"id": s["id"], "section": s.get("section", ""), "title": s["title"], "impact": n, "level": lv}
                for s in live for n, lv in imp.items() if matches(s, n)]
    decisions = jd.get("decisions", [])
    facts = {"feature": feature or jd.get("feature", ""), "generated_at": now(),
             "inputs": {k: v for k, v in {"suite": args.suite, "tc": args.tc, "review": args.review, "judgment": args.judgment}.items() if v},
             "suite": {"name": st["name"], "version": st["version"], "cases": len(live)},
             "feature_decision": decisions[-1] if decisions else None,
             "criteria_note": {"core": "핵심 흐름", "business": "사업 영향", "change": "변경 영향(기존 기능)", "automatable": "자동화 가능"},
             "items": items, "affected": affected, "updates": []}
    write_json(Path(args.out) / "candidates-facts.json", facts)
    cnt = counts([{"h": i["hint"]} for i in items], "h")
    print(f"candidates-facts.json: 후보 {len(items)}개 — " + " · ".join(f"{REC[k][0]} {v}" for k, v in cnt.items())
          + f" · 겹침 {sum(1 for i in items if i['duplicates'])} · 영향받는 기존 케이스 {len({a['id'] for a in affected})}"
          + ("" if decisions else " · 기능 편입 결정 전"))


def validate_candidates(d, cfg):
    errs = []
    for i in d["items"]:
        if i.get("recommendation") not in ("include", "exclude", "defer"):
            errs.append(f"{i['id']}: recommendation은 include/exclude/defer (검토 후보도 하나로 정한다)")
        if not i.get("reason"):
            errs.append(f"{i['id']}: reason(한 줄 이유)이 없음")
        if i.get("recommendation") == "include" and i.get("result") != "pass":
            errs.append(f"{i['id']}: 결과가 Pass가 아닌 TC는 넣기로 추천하지 않는다(나중에)")
        if i.get("recommendation") == "include" and i.get("assumed"):
            errs.append(f"{i['id']}: {', '.join(i['assumed'])} 답 전 임시 가정 기준 결과 — 답을 받고 다시 확인한 뒤 넣는다(나중에)")
        if i.get("recommendation") == "include" and i.get("duplicates") and not i.get("merge_note"):
            errs.append(f"{i['id']}: 기존 케이스와 겹치는데 넣기 — merge_note(왜 따로 두는지)가 필요")
    cap = cfg["regression"].get("max_per_feature")
    n_inc = sum(1 for i in d["items"] if i.get("recommendation") == "include")
    if cap and n_inc > cap and not d.get("cap_override_reason"):
        errs.append(f"넣기 {n_inc}개가 기능당 한도 {cap}개를 넘음 — 줄이거나 cap_override_reason(왜 더 넣어야 하는지)을 쓴다")
    ids = {s for s in (a["id"] for a in d.get("affected", []))}
    for k, u in enumerate(d.get("updates", [])):
        for f in ("id", "target", "why", "changes"):
            if not u.get(f):
                errs.append(f"updates[{k}].{f} 없음")
        if u.get("target") and u["target"] not in ids:
            errs.append(f"updates[{k}]: {u.get('target')}는 영향받는 기존 케이스 목록에 없음")
    return errs


def crit_chips(i, d):
    return "".join(f'<span class="chip {"on" if v else "off"}">{e(d["criteria_note"][k])}</span>' for k, v in i["criteria"].items())


def cmd_render_candidates(args, cfg):
    d = read_json(args.candidates)
    errs = validate_candidates(d, cfg)
    if errs:
        sys.exit("candidates.json 점검 실패:\n  - " + "\n  - ".join(errs))
    out = Path(args.outdir or Path(args.candidates).parent)
    dec = d.get("feature_decision")
    vl = {"include": "편입 가능", "conditional": "조건부 편입", "hold": "편입 보류"}
    banner = (f'<div class="banner ok"><b>기능 편입 결정: {e(vl.get(dec["decision"], dec["decision"]))}</b><span>{e(dec.get("by", ""))} · 승인한 후보는 바로 스위트에 들어간다</span></div>'
              if dec and dec["decision"] in ("include", "conditional") else
              '<div class="banner warn"><b>기능 편입 결정 전</b><span>추천은 미리 볼 수 있지만, 기능이 릴리즈에 들어가기로 정해진 뒤에 스위트에 넣는다.</span></div>')
    groups = {k: [i for i in d["items"] if i["recommendation"] == k] for k in ("include", "defer", "exclude")}
    applied = {x for x in d.get("applied", [])}
    sec = []
    for k, its in groups.items():
        if not its:
            continue
        rows = "".join(f'<tr><td class="mono">{e(i["id"])}{" ✓" if i["id"] in applied else ""}</td><td class="mono">{e(i["tc"]["id"])}</td>'
                       f'<td>{e(i["tc"]["title"])}<div class="muted small">{e(i["tc"]["domain"])} · {e(i["tc"]["section"])} · {e(i["tc"]["type"])}</div>'
                       f'<div>{crit_chips(i, d)}</div></td><td class="c">{e(i["tc"]["priority"])}</td>'
                       f'<td class="c">{e(cfg["suite"]["results"].get(i["result"], i["result"] or "미실행"))}</td>'
                       f'<td>{e(i["reason"])}' + ("".join(f'<div class="muted small">겹침: {e(x["id"])} {e(x["title"])} ({x["ratio"]})</div>' for x in i["duplicates"]))
                       + (f'<div class="muted small">{e(i["merge_note"])}</div>' if i.get("merge_note") else "")
                       + (f'<div class="muted small">{e(i["note"])}</div>' if i.get("note") and i["recommendation"] != "include" else "")
                       + (f'<div class="muted small">기준표 후보: {e(REC[i["hint"]][0])}</div>' if i["hint"] != i["recommendation"] else "") + "</td></tr>" for i in its)
        sec.append(f'<section><h2><span class="flag {REC[k][1]}">{REC[k][0]}</span> {len(its)}개</h2><div class="tbl"><table><thead><tr><th>후보</th><th>TC</th><th>내용·기준</th>'
                   f'<th class="c">우선</th><th class="c">결과</th><th>이유</th></tr></thead><tbody>{rows}</tbody></table></div></section>')
    up = d.get("updates", [])
    up_html = ""
    if up:
        rows = "".join(f'<tr><td class="mono">{e(u["id"])}{" ✓" if u["id"] in applied else ""}</td><td class="mono">{e(u["target"])}</td><td>{e(u["why"])}'
                       + "".join(f'<div class="muted small">{e(f)}: {e(" / ".join(v) if isinstance(v, list) else v)}</div>' for f, v in u["changes"].items())
                       + "</td></tr>" for u in up)
        up_html = (f'<section><h2>기존 케이스 고치기 <span class="muted small">{len(up)}개</span></h2>'
                   f'<div class="tbl"><table><thead><tr><th>번호</th><th>대상</th><th>왜·무엇을</th></tr></thead><tbody>{rows}</tbody></table></div></section>')
    aff = {}
    for a in d.get("affected", []):
        aff.setdefault(a["id"], a)
    aff_html = (f'<details><summary>이 기능의 영향을 받는 기존 회귀 케이스 {len(aff)}개 (기대 결과가 바뀌는지 확인한 목록)</summary><ul class="ev">'
                + "".join(f'<li>{e(a["id"])} {e(a["title"])} <span class="muted small">— {e(a["impact"])}</span>'
                          + (f'<div class="note">{e(d["affected_notes"][a["id"]])}</div>' if a["id"] in d.get("affected_notes", {}) else "") + "</li>" for a in aff.values())
                + "</ul></details>") if aff else ""
    n = {k: len(v) for k, v in groups.items()}
    kpi = ('<div class="kpis">' + "".join(f'<div class="kpi"><b>{v}</b><span>{REC[k][0]}</span></div>' for k, v in n.items())
           + f'<div class="kpi"><b>{len(up)}</b><span>기존 케이스 고치기</span></div><div class="kpi"><b>{d["suite"]["cases"]}</b><span>현재 스위트</span></div></div>')
    rc = cfg["regression"]
    how = (f'<p class="muted">기준: 핵심 흐름(+2) · 사업 영향(+1) · 변경 영향(+1) · 자동화 가능(+1). {rc["include_at"]}점 이상 넣기, {rc["exclude_at"]}점 이하 빼기, '
           f'결과가 Pass가 아니거나 질문 답 전 임시 가정 결과면 나중에. 영역마다 대표 {rc.get("per_section_limit", "-")}개, 기능당 최대 {rc.get("max_per_feature", "-")}개. '
           '승인할 후보 번호를 QA 매니저가 정하면 그것만 스위트에 들어간다.</p>'
           + (f'<div class="banner warn"><b>기능당 한도를 넘겨 추천</b><span>{e(d["cap_override_reason"])}</span></div>' if d.get("cap_override_reason") else ""))
    body = banner + kpi + how + "".join(sec) + up_html + aff_html
    title = f'{d["feature"]} 회귀 편입 후보'
    (out / "candidates.html").write_text(page(title, "회귀 TC 관리자", body, f'<span>{e(d["generated_at"][:16].replace("T", " "))}</span><span>스위트 {e(d["suite"]["name"])} v{d["suite"]["version"]}</span>'), encoding="utf-8")
    md = [f"# {title}", "", ("기능 편입 결정: " + vl.get(dec["decision"], dec["decision"])) if dec else "_기능 편입 결정 전_", ""]
    for k, its in groups.items():
        if its:
            md += [f"## {REC[k][0]} ({len(its)})", ""] + [f"- {i['id']} {i['tc']['id']} {i['tc']['title']} ({i['tc']['priority']}) — {i['reason']}" for i in its] + [""]
    if up:
        md += ["## 기존 케이스 고치기", ""] + [f"- {u['id']} {u['target']} — {u['why']}" for u in up]
    (out / "candidates.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"편입 후보: " + " · ".join(f"{REC[k][0]} {v}" for k, v in n.items()) + f" · 고치기 {len(up)} → {out / 'candidates.html'}")


def cmd_apply(args, cfg):
    d = read_json(args.candidates)
    errs = validate_candidates(d, cfg)
    if errs:
        sys.exit("candidates.json 점검 실패:\n  - " + "\n  - ".join(errs))
    dec = d.get("feature_decision")
    if not (dec and dec["decision"] in ("include", "conditional")) and not args.before_decision:
        sys.exit("기능 편입 결정(편입 가능·조건부 편입)이 아직 없음 — 결정 뒤에 넣는다. 미리 넣으라는 지시가 있으면 --before-decision")
    st = read_json(args.suite)
    tc = {c["id"]: c for c in read_json(d["inputs"]["tc"])["cases"]}
    by = {i["id"]: i for i in d["items"]}
    ups = {u["id"]: u for u in d.get("updates", [])}
    approve = [x.strip() for x in (args.approve or "").split(",") if x.strip()]
    approve_up = [x.strip() for x in (args.approve_updates or "").split(",") if x.strip()]
    bad = [x for x in approve if x not in by] + [x for x in approve_up if x not in ups]
    if bad:
        sys.exit(f"없는 번호: {', '.join(bad)}")
    already = set(d.get("applied", []))
    again = [x for x in approve + approve_up if x in already]
    if again:
        sys.exit(f"이미 스위트에 반영한 번호: {', '.join(again)}")
    off = [x for x in approve if by[x]["recommendation"] != "include"]
    if off and not args.note:
        sys.exit(f"추천이 넣기가 아닌 후보({', '.join(off)})를 승인하려면 --note(이유)가 필요")
    if any(by[x].get("assumed") for x in approve):
        sys.exit("질문 답 전 임시 가정 기준 결과인 TC는 넣지 않는다: " + ", ".join(x for x in approve if by[x].get("assumed")))
    if any(by[x]["result"] != "pass" for x in approve):
        sys.exit("결과가 Pass가 아닌 TC는 넣지 않는다: " + ", ".join(x for x in approve if by[x]["result"] != "pass"))
    pre = cfg["suite"]["id_prefix"]
    nums = [int(m.group(1)) for c in st["cases"] for m in [re.match(rf"{pre}-(\d+)$", c["id"])] if m]
    nxt = max(nums, default=0)
    today = datetime.now().date().isoformat()
    added = []
    for x in approve:
        src = tc[by[x]["tc"]["id"]]
        nxt += 1
        case = {"id": f"{pre}-{nxt:04d}", **{k: src.get(k) for k in ("domain", "section", "type", "title", "preconditions", "steps", "expected", "priority")},
                "automation": "api" if by[x]["mode"] == "api" else "manual", "source": f"{d['feature']} {src['id']}", "added_at": today, "last_result": "pass"}
        st["cases"].append({k: v for k, v in case.items() if v not in (None, "", [])})
        added.append(case["id"])
    changed = []
    for x in approve_up:
        u = ups[x]
        target = next(c for c in st["cases"] if c["id"] == u["target"])
        target.update(u["changes"])
        target["updated_at"] = today
        changed.append(u["target"])
    st["version"] += 1
    st["updated_at"] = now()
    if added:
        st["history"].append({"at": now(), "version": st["version"], "change": "add", "ids": added, "by": args.by,
                              "from": f"{d['feature']} " + ", ".join(by[x]["tc"]["id"] for x in approve), **({"note": args.note} if args.note else {})})
    if changed:
        st["history"].append({"at": now(), "version": st["version"], "change": "update", "ids": changed, "by": args.by,
                              "from": "; ".join(f"{ups[x]['target']}: {ups[x]['why']}" for x in approve_up), **({"note": args.note} if args.note else {})})
    write_json(args.suite, st)
    d["applied"] = sorted(already | set(approve) | set(approve_up))
    d.setdefault("approvals", []).append({"at": now(), "by": args.by, "approve": approve, "approve_updates": approve_up, "added": added, **({"note": args.note} if args.note else {})})
    write_json(args.candidates, d)
    print(f"스위트 v{st['version']}: 추가 {len(added)}({', '.join(added) or '-'}) · 고침 {len(changed)}({', '.join(changed) or '-'})")
    args.outdir = None
    cmd_render(args, cfg)
    args.outdir = None
    cmd_render_candidates(args, cfg)


# ── 릴리즈 범위 ──
def cmd_select(args, cfg):
    sel = cfg["regression"]["select"]
    st = read_json(args.suite)
    live = [c for c in st["cases"] if not c.get("retired")]
    feats = []
    paths = list(args.features)
    if args.ledger:
        led = read_json(args.ledger)
        paths += [f["judgment"] for f in led.get("features", []) if f.get("decision") in ("include", "conditional") and f.get("judgment")]
    for p in dict.fromkeys(paths):
        jd = read_json(p)
        dec = (jd.get("decisions") or [None])[-1]
        feats.append({"feature": jd.get("feature", ""), "judgment": p, "decision": dec["decision"] if dec else None})
    ans = [read_json(a) for a in args.analysis]
    imp = {}
    for p in dict.fromkeys(paths):
        for n, lv in impact_names(read_json(p), []).items():
            imp.setdefault(n, lv)
    for n, lv in impact_names({}, ans).items():
        imp[n] = lv if imp.get(n) != "direct" else "direct"
    names = [f["feature"] for f in feats]
    cases = []
    for c in live:
        hit = [(n, lv) for n, lv in imp.items() if matches(c, n)]
        own = any(nm and nm in c.get("source", "") for nm in names)
        if c.get("priority") in sel["must_priorities"]:
            cat, why = "must", f"{c['priority']}은 매 릴리즈 실행" + (" · 영향: " + ", ".join(n for n, _ in hit) if hit else "")
        elif sel.get("always_last_failed") and c.get("last_result") == "fail":
            cat, why = "must", "지난 회귀에서 Fail"
        elif own:
            cat, why = "direct", "이번 릴리즈 기능에서 편입된 케이스"
        elif any(lv == "direct" for _, lv in hit):
            cat, why = "direct", "직접 영향: " + ", ".join(n for n, lv in hit if lv == "direct")
        elif hit and c.get("priority") in sel["indirect_priorities"]:
            cat, why = "indirect", "간접 영향: " + ", ".join(n for n, _ in hit)
        elif hit:
            cat, why = "skip", f"간접 영향({', '.join(n for n, _ in hit)})이지만 {c.get('priority')} — 간접 영향은 {'·'.join(sel['indirect_priorities'])}만 실행"
        else:
            cat, why = "skip", "이번 릴리즈 변경과 관련 없음"
        cases.append({"id": c["id"], "domain": c.get("domain", ""), "section": c.get("section", ""), "title": c["title"],
                      "priority": c.get("priority", ""), "automation": c.get("automation", ""), "category": cat, "reason": why})
    facts = {"release": args.release, "generated_at": now(), "suite": {"path": args.suite, "name": st["name"], "version": st["version"], "cases": len(live)},
             "features": feats, "impacts": [{"name": n, "level": lv} for n, lv in imp.items()], "cases": cases, "overrides": []}
    write_json(Path(args.out) / "selection-facts.json", facts)
    cnt = counts([{"c": c["category"]} for c in cases], "c")
    pend = [f["feature"] for f in feats if f["decision"] not in ("include", "conditional")]
    print(f"selection-facts.json: {len(cases)}개 중 " + " · ".join(f"{CAT[k][0]} {cnt.get(k, 0)}" for k in CAT)
          + (f" · 편입 결정 전 기능: {', '.join(pend)}" if pend else ""))


def cmd_render_selection(args, cfg):
    d = read_json(args.selection)
    errs = []
    by = {c["id"]: c for c in d["cases"]}
    for k, o in enumerate(d.get("overrides", [])):
        if o.get("id") not in by or o.get("category") not in CAT or not o.get("reason"):
            errs.append(f"overrides[{k}]: 있는 id, category({'/'.join(CAT)}), reason 필요")
    if errs:
        sys.exit("selection.json 점검 실패:\n  - " + "\n  - ".join(errs))
    for o in d.get("overrides", []):
        by[o["id"]].update(category=o["category"], reason=o["reason"], overridden=True)
    run = [c for c in d["cases"] if c["category"] != "skip"]
    out = Path(args.outdir or Path(args.selection).parent)
    out.mkdir(parents=True, exist_ok=True)
    st = read_json(d["suite"]["path"])
    full = {c["id"]: c for c in st["cases"]}
    xlsx.write(out / "regression-run.xlsx", suite_sheets([dict(full[c["id"]], result="", issue="") for c in run], cfg, results=True), title=f'{d["release"]} 회귀 실행')
    cnt = {k: [c for c in d["cases"] if c["category"] == k] for k in CAT}
    al = cfg["suite"]["automation"]
    auto = sum(1 for c in run if c.get("automation") == "api")
    pend = [f for f in d["features"] if f["decision"] not in ("include", "conditional")]
    vl = {"include": "편입 가능", "conditional": "조건부 편입", "hold": "편입 보류", None: "결정 전"}
    feat = "".join(f'<li>{e(f["feature"])} <span class="flag {"green" if f["decision"] in ("include", "conditional") else "amber"}">{e(vl.get(f["decision"], f["decision"]))}</span></li>' for f in d["features"])
    banner = (f'<div class="banner warn"><b>편입 결정 전 기능이 있음: {e(", ".join(f["feature"] for f in pend))}</b><span>그 기능이 빠지면 범위를 다시 고른다(뺀 빌드의 재회귀 범위는 릴리즈 판정관이 정함).</span></div>' if pend else "")
    kpi = ('<div class="kpis">' + f'<div class="kpi"><b>{len(run)}/{len(d["cases"])}</b><span>이번에 실행</span></div>'
           + "".join(f'<div class="kpi"><b>{len(v)}</b><span>{CAT[k][0]}</span></div>' for k, v in cnt.items())
           + f'<div class="kpi"><b>{auto}</b><span>그중 API 자동화</span></div></div>')
    imp = "".join(f'<span class="chip">{e(i["name"])} · {"직접" if i["level"] == "direct" else "간접"}</span>' for i in d["impacts"])
    sec = []
    for k, cs in cnt.items():
        if not cs:
            continue
        rows = "".join(f'<tr><td class="mono">{e(c["id"])}</td><td>{e(c["domain"])}</td><td>{e(c["section"])}</td><td>{e(c["title"])}'
                       f'<div class="muted small">{e(c["reason"])}{" (조정)" if c.get("overridden") else ""}</div></td><td class="c">{e(c["priority"])}</td>'
                       f'<td class="c">{e(al.get(c["automation"], c["automation"]))}</td></tr>' for c in cs)
        body = (f'<div class="tbl"><table><thead><tr><th>CaseID</th><th>Domain</th><th>section</th><th>title·이유</th><th class="c">우선</th><th class="c">자동화</th></tr></thead><tbody>{rows}</tbody></table></div>')
        head = f'<h2><span class="flag {CAT[k][1]}">{CAT[k][0]}</span> {len(cs)}개</h2>'
        sec.append(f'<section>{head}{body}</section>' if k != "skip" else f'<section><details><summary>{CAT[k][0]} {len(cs)}개 — 이유와 함께</summary>{body}</details></section>')
    body = (banner + kpi + f'<section><h2>이번 릴리즈 기능</h2><ul class="ev">{feat}</ul><div>{imp}</div></section>' + "".join(sec))
    title = f'{d["release"]} 회귀 범위'
    (out / "selection.html").write_text(page(title, "회귀 TC 관리자", body, f'<span>{e(d["generated_at"][:16].replace("T", " "))}</span><span>스위트 {e(d["suite"]["name"])} v{d["suite"]["version"]}</span>'), encoding="utf-8")
    md = [f"# {title}", "", f"{len(d['cases'])}개 중 {len(run)}개 실행 — " + " · ".join(f"{CAT[k][0]} {len(v)}" for k, v in cnt.items()), ""]
    for k, cs in cnt.items():
        if cs:
            md += [f"## {CAT[k][0]} ({len(cs)})", ""] + [f"- {c['id']} {c['title']} ({c['priority']}) — {c['reason']}" for c in cs] + [""]
    (out / "selection.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"회귀 범위: {len(run)}/{len(d['cases'])}개 (" + " · ".join(f"{CAT[k][0]} {len(v)}" for k, v in cnt.items()) + f") → {out / 'regression-run.xlsx'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import"); p.add_argument("source"); p.add_argument("--out", required=True); p.add_argument("--name")
    p = sub.add_parser("render"); p.add_argument("suite"); p.add_argument("outdir", nargs="?")
    p = sub.add_parser("candidates"); p.add_argument("--suite", required=True); p.add_argument("--tc", required=True)
    p.add_argument("--review"); p.add_argument("--judgment"); p.add_argument("--analysis", nargs="*", default=[]); p.add_argument("--out", required=True)
    p = sub.add_parser("render-candidates"); p.add_argument("candidates"); p.add_argument("outdir", nargs="?")
    p = sub.add_parser("apply"); p.add_argument("candidates"); p.add_argument("--suite", required=True)
    p.add_argument("--approve"); p.add_argument("--approve-updates", dest="approve_updates"); p.add_argument("--by", required=True)
    p.add_argument("--note"); p.add_argument("--before-decision", dest="before_decision", action="store_true")
    p = sub.add_parser("select"); p.add_argument("--suite", required=True); p.add_argument("--features", nargs="*", default=[])
    p.add_argument("--ledger"); p.add_argument("--analysis", nargs="*", default=[]); p.add_argument("--release", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("render-selection"); p.add_argument("selection"); p.add_argument("outdir", nargs="?")
    for s in sub.choices.values():
        s.add_argument("--config")
    args = ap.parse_args()
    cfg = load_config(args.config)
    {"import": cmd_import, "render": cmd_render, "candidates": cmd_candidates, "render-candidates": cmd_render_candidates,
     "apply": cmd_apply, "select": cmd_select, "render-selection": cmd_render_selection}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
