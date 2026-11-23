#!/usr/bin/env python3
"""릴리즈 판정관 도구: 신규 기능 판정과 회귀 검토를 모아(collect) 배포 추천 판정서를 그리고(render), 사람의 결정을 기록한다(decide).

사용법:
  python3 release_judge.py collect --review <회귀 검토 review.json> --selection <selection.json>
                                   [--ledger <릴리즈 기록 features.json>] [--analysis "<기능>=<기획분석서.json>" ...] --out <폴더>
      → <폴더>/release-facts.json  (요소 R1~R5, 릴리즈 이슈 후보(어느 기능 때문인지 포함), 추천 후보 hint, 기능별 '빼면 다시 돌릴 회귀 범위')
  python3 release_judge.py render <release-judgment.json> [출력 폴더]
      → release-judgment.html, release-judgment.md, rerun-<기능>.json (기능을 뺀 빌드의 회귀 범위 — 회귀 자동화 관리자 run --selection에 그대로 쓴다)
  python3 release_judge.py decide <release-judgment.json> --decision go|go_conditional|go_without|no_go --by "<정한 사람>"
                                  [--note "<이유>"] [--accept K-01,K-02] [--exclude "<기능>"]
      → 결정을 더하고 다시 그린다. 판정서 화면(링크)에서 제출된 결정을 옮길 때는 --source <문서 id> --submitted-at <시각>을 붙인다.

경로(판정서·스위트)는 현재 폴더 기준이다. 공통 옵션: --config <qa-orchestra.config.json>
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
VERDICTS = ["go", "go_conditional", "go_without", "no_go"]
STATUS = {"met": ("충족", "green"), "conditional": ("조건부", "amber"), "unmet": ("미충족", "red"), "info": ("참고", "muted")}
RANK = {"met": 0, "info": 0, "conditional": 1, "unmet": 2}
FEATURE_DECISION = {"include": "편입 가능", "conditional": "조건부 편입", "hold": "편입 보류"}
CAUSE = {"change": "이번 변경으로 깨짐", "existing": "전부터 있던 결함", "tc_outdated": "TC가 정책·명세와 다름", "env": "환경·데이터 문제"}
CONF = {"high": "근거 충분", "mid": "근거 일부", "low": "확인 필요"}


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


def norm(s):
    return re.sub(r"\([^)]*\)|[\s·,/()\-]", "", str(s or ""))


def matches(case, name):
    core = norm(name)
    sec = norm(case.get("section", ""))
    return bool(sec) and (sec in core or core in sec or all(t in name for t in re.findall(r"\w{2,}", case.get("section", ""))))


def slug(name):
    return re.sub(r"[\s/\\:]+", "-", name.strip()) or "feature"


def element(eid, name, status, summary, evidence, refs=()):
    return {"id": eid, "name": name, "status": status, "summary": summary, "evidence": list(evidence), "refs": list(refs)}


# ── collect ──
def load_features(sel, ledger, analyses):
    """이번 릴리즈 기능: selection.features + 릴리즈 기록. 판정서를 다시 읽어 최신 결정과 영향 기능을 얻는다."""
    feats = {}
    for f in sel.get("features", []) + (read_json(ledger).get("features", []) if ledger else []):
        name = f.get("feature")
        if name:
            feats.setdefault(name, {"feature": name, "judgment": f.get("judgment", "")})
    out = []
    for f in feats.values():
        jd = read_json(f["judgment"]) if f["judgment"] and Path(f["judgment"]).exists() else {}
        last = (jd.get("decisions") or [None])[-1]
        impacts = [im["name"] for im in jd.get("impacts", [])]
        if f["feature"] in analyses:
            for g in read_json(analyses[f["feature"]]).get("impacts", []):
                impacts += [it["name"] for it in g.get("items", [])]
        conds = {c["id"]: c for c in jd.get("conditions", [])}
        out.append(dict(f, found=bool(jd), recommendation=(jd.get("recommendation") or {}).get("verdict"),
                        decision=last["decision"] if last else None, decided_by=last.get("by", "") if last else "",
                        accepted=[{"id": k, "what": conds[k]["what"]} for k in (last or {}).get("conditions_accepted", []) if k in conds],
                        impacts=list(dict.fromkeys(impacts))))
    if len(out) == 1:
        # 기능이 하나면 이번 릴리즈의 영향 기능(분석서의 간접 영향 포함)은 모두 그 기능 몫이다
        out[0]["impacts"] = list(dict.fromkeys(out[0]["impacts"] + [im["name"] for im in sel.get("impacts", [])]))
    return out


def owned_by(feats, case, source):
    """그 기능 때문에 깨졌을 수 있는 기능 목록: 기능에서 편입된 케이스이거나 기능의 영향 영역."""
    names = [i["name"] for i in case.get("impacts", [])] or [n for f in feats for n in f["impacts"] if matches(case, n)]
    return [f["feature"] for f in feats if f["feature"] in source or any(n in f["impacts"] for n in names)]


def rerun_scope(feat, cases, sources):
    """기능을 뺀 빌드에서 다시 돌릴 회귀 범위 = 뺀 기능의 영향 케이스 + 필수(P1·지난 Fail). 뺀 기능에서 편입된 케이스는 빠진다."""
    rerun, keep, drop = [], [], []
    for c in cases:
        if c["category"] == "skip":
            continue
        hit = [n for n in feat["impacts"] if matches(c, n)]
        if feat["feature"] in sources.get(c["id"], ""):
            drop.append({"id": c["id"], "title": c["title"], "why": "빠진 기능에서 편입된 케이스"})
        elif c["category"] == "must":
            rerun.append({"id": c["id"], "title": c["title"], "priority": c.get("priority", ""), "automation": c.get("automation", ""),
                          "why": "필수: " + c.get("reason", ""), "kind": "must"})
        elif hit:
            rerun.append({"id": c["id"], "title": c["title"], "priority": c.get("priority", ""), "automation": c.get("automation", ""),
                          "why": "빠진 기능 영향: " + ", ".join(hit), "kind": "impact"})
        else:
            keep.append({"id": c["id"], "title": c["title"], "result": c.get("result", ""),
                         "why": "빠진 기능과 관계없음 — " + ("이번 결과 그대로" if c.get("result") in ("pass", "fail") else "다시 안 돌림")})
    return {"feature": feat["feature"], "file": f"rerun-{slug(feat['feature'])}.json", "rerun": rerun, "keep": keep, "drop": drop}


def collect(args, cfg):
    rc = cfg["release"]
    rv = read_json(args.review)
    sel = read_json(args.selection)
    analyses = dict(x.split("=", 1) for x in args.analysis if "=" in x)
    feats = load_features(sel, args.ledger, analyses)
    suite_path = sel.get("suite", {}).get("path", "")
    suite = read_json(suite_path) if suite_path and Path(suite_path).exists() else {"cases": []}
    sources = {c["id"]: c.get("source", "") for c in suite["cases"]}
    over = {o["id"]: o for o in sel.get("overrides", [])}
    result = {c["id"]: c.get("result", "") for c in rv.get("cases", [])}
    cases = [dict(c, **({"category": over[c["id"]]["category"], "reason": over[c["id"]]["reason"]} if c["id"] in over else {}),
                  result=result.get(c["id"], "")) for c in sel["cases"]]
    by_id = {c["id"]: c for c in cases}
    els, blockers = [], []

    def add(level, title, el, refs, features=()):
        blockers.append({"id": "", "level": level, "title": title, "element": el, "refs": [r for r in refs if r], "features": list(features)})

    # R1 기능 판정
    ev, worst = [], "met"
    for f in feats:
        d = f["decision"]
        s = {"include": "met", "conditional": "conditional"}.get(d, "unmet")
        worst = max(worst, s, key=RANK.get)
        rec = FEATURE_DECISION.get(f["recommendation"], "판정서 없음" if not f["found"] else "추천 없음")
        ev.append(f"{f['feature']}: " + (f"{FEATURE_DECISION[d]} 결정({f['decided_by']})" if d else f"결정 전 — 신규 기능 판정관 추천 {rec}")
                  + (f" · 받아들인 조건 {', '.join(c['id'] for c in f['accepted'])}" if f["accepted"] else ""))
        if d == "hold":
            add("block", f"{f['feature']} — 편입 보류로 결정됨(이 빌드에 들어 있으면 뺀다)", "R1", [f["judgment"]], [f["feature"]])
        elif not d:
            add("block", f"{f['feature']} — 편입 결정 전(신규 기능 판정관 추천: {rec})", "R1", [f["judgment"]], [f["feature"]])
        elif d == "conditional":
            add("cond", f"{f['feature']} — 조건부 편입: 받아들인 조건 " + (", ".join(f"{c['id']} {c['what']}" for c in f["accepted"]) or "기록 없음")
                + " — 배포 전 확인", "R1", [c["id"] for c in f["accepted"]], [f["feature"]])
    els.append(element("R1", "기능 판정", worst if feats else "info",
                       f"기능 {len(feats)}개 — " + " · ".join(f"{n} {sum(1 for f in feats if f['decision'] == k)}" for k, n in
                                                           (("include", "편입"), ("conditional", "조건부"), ("hold", "보류"), (None, "결정 전"))) if feats else "기능 없음(회귀만)",
                       ev, [f["feature"] for f in feats]))

    # R2 회귀 실행 완료
    must_nr = [x for x in rv.get("not_run", []) if by_id.get(x["id"], {}).get("category") == "must"]
    rest_nr = [x for x in rv.get("not_run", []) if x not in must_nr]
    att = rv.get("attention", [])
    must_att = [a for a in att if by_id.get(a["id"], {}).get("category") == "must"]
    s = "unmet" if (must_nr or must_att) else "conditional" if (rest_nr or att) else "met"
    els.append(element("R2", "회귀 실행 완료", s,
                       f"범위 {rv.get('scope', len(cases))}개 중 결과 없음 {len(rv.get('not_run', []))}(필수 {len(must_nr)}) · 결과로 치지 않는 것 {len(att)}",
                       [f"{x['id']} {x['priority']} {x['title']} — {x['why']}" + (" · 필수" if x in must_nr else "") for x in rv.get("not_run", [])]
                       + [f"{a['id']} {a['label']} — {a.get('message', '')}" for a in att],
                       [x["id"] for x in rv.get("not_run", [])] + [a["id"] for a in att]))

    if must_nr:
        ids = [x["id"] for x in must_nr]
        add("block", f"필수 회귀 케이스 결과 없음: {', '.join(ids)} — " + " · ".join(x["title"] for x in must_nr), "R2", ids)
    if rest_nr:
        ids = [x["id"] for x in rest_nr]
        add("cond", f"그 밖의 회귀 케이스 {len(ids)}개 결과 없음 ({', '.join(ids)})", "R2", ids)
    for a in att:
        add("block" if a in must_att else "cond", f"{a['id']} {a['label']} — 결과로 치지 않음: {a['title']}", "R2", [a["id"]])

    # R3 회귀 Fail
    reviews = {r["id"]: r for r in rv.get("fail_reviews", [])}
    ev, worst = [], "met"
    for f in rv.get("fails", []):
        r = reviews.get(f["id"])
        pr = f.get("priority", "")
        if not r:
            lvl, what = ("block" if pr in rc["unsure_block_priorities"] else "cond"), "원인 검토 전"
        elif r.get("confidence") == "low":
            lvl, what = ("block" if pr in rc["unsure_block_priorities"] else "cond"), f"{CAUSE[r['cause']]}? — 확인 필요"
        elif r["cause"] in ("change", "existing"):
            lvl, what = ("block" if pr in rc["fail_block_priorities"] else "cond"), f"결함 — {CAUSE[r['cause']]}"
        else:
            lvl, what = "cond", CAUSE[r["cause"]]
        fs = owned_by(feats, f, f.get("source", "")) if r and r["cause"] == "change" else []
        worst = max(worst, "unmet" if lvl == "block" else "conditional", key=RANK.get)
        q = (r or {}).get("question")
        ev.append(f"{f['id']} {pr} {f['title']} — {what}" + (f" ({CONF.get(r.get('confidence'), '')})" if r else "")
                  + (f" · 관련 기능 {', '.join(fs)}" if fs else "") + (f" · 질문({q['to']}): {q['ask']}" if q else ""))
        add(lvl, f"{f['id']} {pr} Fail — {f['title']} ({what})", "R3", [f["id"]], fs)
    els.append(element("R3", "회귀 Fail", worst, f"Fail {len(rv.get('fails', []))}개" + (" — " + " · ".join(
        f"{CAUSE[k]} {n}" for k in CAUSE if (n := sum(1 for r in reviews.values() if r['cause'] == k))) if reviews else ""),
        ev, [f["id"] for f in rv.get("fails", [])]))

    # R4 운영 결함
    sev = rc["ops_check_severities"]
    open_ops = [o for o in rv.get("ops", []) if not o.get("closed")]
    check = [o for o in open_ops if o.get("severity") in sev]
    els.append(element("R4", "열린 운영 결함", "conditional" if check else "met" if rv.get("ops") else "info",
                       f"운영 결함 {len(rv.get('ops', []))}건 중 열림 {len(open_ops)}({'·'.join(sev)} {len(check)})" if rv.get("ops") else "운영 결함 자료 없음",
                       [f"{o['key']} {o['severity']} · {o['status']} — {o['summary']}" + (f" · 수정: {o['fix']}" if o.get("fix") else "") for o in open_ops],
                       [o["key"] for o in open_ops]))
    for o in check:
        add("cond", f"운영 결함 {o['key']}({o['severity']}·{o['status']}) — 이번 빌드에 수정이 들어가는지 확인: {o['summary']}", "R4", [o["key"]])

    # R5 회귀 검토 상태
    sm = rv.get("summary", {})
    qs = [f"{r['id']} 질문({r['question']['to']}): {r['question']['ask']}" for r in rv.get("fail_reviews", []) if r.get("question")]
    els.append(element("R5", "회귀 검토 상태", "conditional" if sm.get("status") == "needs_work" else "met",
                       "남은 확인 뒤 판정(needs_work)" if sm.get("status") == "needs_work" else "판정관에게 넘김(ready)",
                       [sm.get("headline", "")] + qs, []))

    for i, b in enumerate(sorted(blockers, key=lambda b: b["level"] != "block")):
        b["id"] = f"B-{i + 1:02d}"
    blockers.sort(key=lambda b: b["id"])
    blocks = [b for b in blockers if b["level"] == "block"]
    if any(not b["features"] for b in blocks):
        hint = "no_go"
    elif blocks:
        hint = "go_without"
    elif blockers:
        hint = "go_conditional"
    else:
        hint = "go"
    facts = {"release": sel.get("release", rv.get("release", "")), "generated_at": now(),
             "inputs": {k: v for k, v in {"review": args.review, "selection": args.selection, "ledger": args.ledger}.items() if v},
             "sheet_url": rv.get("sheet_url", ""), "suite": sel.get("suite", {}),
             "counts": dict(rv.get("counts", {}), scope=rv.get("scope", 0)),
             "features": [{k: f[k] for k in ("feature", "judgment", "recommendation", "decision", "decided_by", "accepted", "impacts")} for f in feats],
             "elements": els, "hint": hint, "hint_exclude": sorted({x for b in blocks for x in b["features"]}) if hint == "go_without" else [],
             "blockers": blockers, "exclusions": [rerun_scope(f, cases, sources) for f in feats],
             "selection": {"release": sel.get("release", ""), "suite": sel.get("suite", {}), "impacts": sel.get("impacts", []), "cases": cases}}
    write_json(Path(args.out) / "release-facts.json", facts)
    vl = rc["verdicts"]
    print(f"릴리즈 이슈 후보: 릴리즈 불가 {len(blocks)} · 조건부 {len(blockers) - len(blocks)}")
    for x in facts["exclusions"]:
        print(f"  {x['feature']} 빼면: 다시 돌림 {len(x['rerun'])} · 결과 그대로 {len(x['keep'])} · 빠짐 {len(x['drop'])}")
    print(f"release-facts.json: 추천 후보 {vl[hint]}" + (f"({', '.join(facts['hint_exclude'])} 빼기)" if facts["hint_exclude"] else "")
          + " — " + " · ".join(f"{el['id']} {el['name']} {STATUS[el['status']][0]}" for el in els))


# ── render ──
e = lambda s: html.escape(str(s if s is not None else ""))


def point_parts(p):
    label, body = (p.split(": ", 1) if ": " in p[:40] else ("", p))
    lines = [x.strip() for x in re.split(r"(?<=다)\.\s+|\s+—\s+", body) if x.strip()]
    return label, lines


def validate(j, cfg):
    errs = []
    owners = cfg["release"]["owners"]
    rec = j.get("recommendation") or {}
    feats = {f["feature"] for f in j.get("features", [])}
    opts = {x["feature"] for x in j.get("exclusions", [])}
    v = rec.get("verdict")
    if v not in VERDICTS:
        errs.append(f"recommendation.verdict는 {'/'.join(VERDICTS)}")
    if not rec.get("headline") or len(rec["headline"]) > 70:
        errs.append("recommendation.headline: 70자 이내 한 줄")
    if not 2 <= len(rec.get("points", [])) <= 6:
        errs.append("recommendation.points: 2~6개('<라벨>: <내용>', 근거 R·B 번호 포함)")
    if v and v != j.get("hint") and not rec.get("deviation_reason"):
        errs.append(f"추천({v})이 기준표 후보({j.get('hint')})와 다름 — recommendation.deviation_reason 필요")
    exclude = rec.get("exclude", [])
    bad = [x for x in exclude if x not in opts]
    if bad:
        errs.append(f"recommendation.exclude: 이번 릴리즈 기능이 아님 {', '.join(bad)}")
    if v == "go_without" and not exclude:
        errs.append("기능 빼고 Go면 recommendation.exclude(뺄 기능)가 필요")
    if v != "go_without" and exclude:
        errs.append("recommendation.exclude는 기능 빼고 Go일 때만 쓴다(다른 선택지는 alternatives에)")
    bl = j.get("blockers", [])
    bids = {b.get("id") for b in bl}
    for i, b in enumerate(bl):
        if b.get("level") not in ("block", "cond") or not b.get("title"):
            errs.append(f"blockers[{i}]: level(block/cond)·title 필요")
        bad = [x for x in b.get("features", []) if x not in feats]
        if bad:
            errs.append(f"blockers[{i}] {b.get('id')}: 없는 기능 {', '.join(bad)}")
    blocks = [b for b in bl if b.get("level") == "block"]
    if v == "go" and bl:
        errs.append("Go인데 릴리즈 이슈가 남아 있음 — 해결됐으면 blockers에서 뺀다")
    if v == "go_conditional" and blocks:
        errs.append("조건부 Go인데 릴리즈 불가 이슈가 있음 — No-Go 또는 기능 빼고 Go")
    if v == "go_conditional" and not bl:
        errs.append("조건부 Go인데 조건부 이슈가 없음 — Go")
    if v == "no_go" and not blocks:
        errs.append("No-Go인데 릴리즈 불가(block) 이슈가 없음 — 무엇 때문에 배포할 수 없는지 blockers에 적는다")
    if v == "go_without":
        left = [b["id"] for b in blocks if not b.get("features") or not set(b["features"]) <= set(exclude)]
        if left:
            errs.append(f"기능 빼고 Go인데 빼도 남는 릴리즈 불가 이슈 {', '.join(left)} — No-Go 또는 이슈 정리")
    conds = j.get("conditions", [])
    if v != "go" and not conds:
        errs.append("Go가 아니면 conditions(배포 전에 할 일)가 필요")
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
        if not c.get("resolves"):
            errs.append(f"conditions[{i}] {c.get('id')}: resolves(해결하는 릴리즈 이슈 B-번호)가 없음")
        elif [x for x in c["resolves"] if x not in bids]:
            errs.append(f"conditions[{i}] {c.get('id')}: 없는 릴리즈 이슈 {', '.join(x for x in c['resolves'] if x not in bids)}")
        if c.get("rerun") and c["rerun"] not in opts:
            errs.append(f"conditions[{i}] {c.get('id')}: rerun은 뺄 기능 이름")
    solved = {x for c in conds for x in c.get("resolves", [])}
    errs += [f"릴리즈 이슈 {b['id']}를 해결할 일(conditions)이 없음" for b in bl if b.get("id") not in solved]
    for x in exclude:
        if not any(c.get("rerun") == x for c in conds):
            errs.append(f"{x}: 빼면 재회귀가 필요 — conditions에 rerun: \"{x}\"인 할 일(뺀 빌드로 재회귀)을 넣는다")
    for i, a in enumerate(j.get("alternatives", [])):
        if not a.get("option") or a.get("verdict") not in VERDICTS:
            errs.append(f"alternatives[{i}]: option·verdict 필요")
        bad = [x for x in a.get("exclude", []) if x not in opts]
        if bad:
            errs.append(f"alternatives[{i}]: 없는 기능 {', '.join(bad)}")
    for i, d in enumerate(j.get("decisions", [])):
        if d.get("decision") not in VERDICTS or not d.get("by"):
            errs.append(f"decisions[{i}]: decision·by가 필요")
        if d.get("decision") != v and not d.get("note"):
            errs.append(f"decisions[{i}]: 추천과 다른 결정에는 note(이유)가 필요")
        if d.get("decision") == "go_without" and not d.get("exclude"):
            errs.append(f"decisions[{i}]: 기능 빼고 Go면 뺄 기능(exclude)이 필요")
        bad = [x for x in d.get("exclude", []) if x not in opts] + [x for x in d.get("conditions_accepted", []) if x not in ids]
        if bad:
            errs.append(f"decisions[{i}]: 없는 기능·조건 {', '.join(bad)}")
    return errs


def pill(status):
    label, cls = STATUS[status]
    return f'<span class="flag {cls}">{label}</span>'


VCLS = {"go": "ok", "go_conditional": "warn", "go_without": "warn", "no_go": "bad"}


def rerun_selection(j, x):
    """기능을 뺀 빌드의 회귀 범위를 selection.json 형식으로 — 회귀 자동화 관리자·검토자가 그대로 쓴다."""
    s = j["selection"]
    why = {r["id"]: r for r in x["rerun"]}
    other = {k["id"]: k["why"] for k in x["keep"]} | {d["id"]: d["why"] for d in x["drop"]}
    cases = []
    for c in s["cases"]:
        c = {k: v for k, v in c.items() if k != "result"}
        if c["id"] in why:
            c["reason"] = why[c["id"]]["why"]
        else:
            c["category"], c["reason"] = "skip", other.get(c["id"], c.get("reason", ""))
        cases.append(c)
    feats = [f for f in j["features"] if f["feature"] != x["feature"]]
    return {"release": f'{s["release"]} — {x["feature"]} 뺀 빌드 재회귀', "generated_at": now(), "suite": s["suite"],
            "features": [{"feature": f["feature"], "judgment": f["judgment"], "decision": f["decision"]} for f in feats],
            "impacts": s["impacts"], "excluded": x["feature"], "cases": cases, "overrides": []}


def render(path, outdir, cfg):
    j = read_json(path)
    errs = validate(j, cfg)
    if errs:
        sys.exit("release-judgment.json 점검 실패:\n  - " + "\n  - ".join(errs))
    vl = cfg["release"]["verdicts"]
    owners = cfg["release"]["owners"]
    rec = j["recommendation"]
    out = Path(outdir or Path(path).parent)
    out.mkdir(parents=True, exist_ok=True)
    decisions = j.get("decisions", [])
    last = decisions[-1] if decisions else None
    ex_txt = lambda xs: f" — {', '.join(xs)} 빼기" if xs else ""

    if last:
        diff = " (추천과 다름)" if last["decision"] != rec["verdict"] else ""
        dec_html = (f'<div class="decision {VCLS[last["decision"]]}"><span class="eyebrow">사람의 결정</span><b>{e(vl[last["decision"]])}{e(diff)}</b>'
                    f'<span>{e(last["by"])} · {e(last["at"][:16].replace("T", " "))}</span>'
                    + (f'<span>뺄 기능: {e(", ".join(last["exclude"]))}</span>' if last.get("exclude") else "")
                    + (f'<span>{e(last["note"])}</span>' if last.get("note") else "")
                    + (f'<span>받아들인 조건: {e(", ".join(last["conditions_accepted"]))}</span>' if last.get("conditions_accepted") else "")
                    + "</div>")
    else:
        dec_html = ('<div class="decision pending"><span class="eyebrow">사람의 결정</span><b>아직 결정 전</b>'
                    '<span>QA 매니저가 추천안을 보고 정한다 — 판정관에게 "Go / 조건부 Go / 기능 빼고 Go / No-Go"와 이유를 말하거나 아래 칸에 남기면 기록된다</span></div>')

    pts = []
    for p in rec["points"]:
        label, lines = point_parts(p)
        pts.append("<li>" + (f"<b>{e(label)}</b>" if label else "") + "".join(f"<div>{e(x)}</div>" for x in lines) + "</li>")
    dev = f'<p class="muted small">기준표 후보는 {e(vl[j["hint"]])}였지만 다르게 추천: {e(rec["deviation_reason"])}</p>' if rec.get("deviation_reason") else ""
    head = (f'<div class="verdict {VCLS[rec["verdict"]]}"><span class="eyebrow">릴리즈 판정관 추천</span><b>{e(vl[rec["verdict"]])}{e(ex_txt(rec.get("exclude")))}</b>'
            f'<span>{e(rec["headline"])}</span><ul class="pts">{"".join(pts)}</ul>{dev}</div>')

    cnt = j.get("counts", {})
    bl = j.get("blockers", [])
    n_block = sum(1 for b in bl if b["level"] == "block")
    sheet = j.get("sheet_url", "")
    feats = j.get("features", [])

    def box(n, label, cls="", href=""):
        if href:
            return f'<a class="kpi link{cls}" href="{e(href)}" target="_blank" rel="noopener"><b>{e(n)}</b><span>{e(label)}</span></a>'
        return f'<div class="kpi{cls}"><b>{e(n)}</b><span>{e(label)}</span></div>'
    decided = sum(1 for f in feats if f["decision"] in ("include", "conditional"))
    kpis = ('<div class="kpis">' + box(f"{decided}/{len(feats)}", "기능 편입 결정", " caution" if decided < len(feats) else "")
            + box(f"{cnt.get('pass', 0)}/{cnt.get('scope', 0)}", "회귀 Pass · 결과 시트" if sheet else "회귀 Pass", "", sheet)
            + box(cnt.get("fail", 0), "회귀 Fail", " warn" if cnt.get("fail") else "")
            + box(cnt.get("not_run", 0), "결과 없음", " caution" if cnt.get("not_run") else "")
            + box(n_block, "릴리즈 불가 이슈", " warn" if n_block else "") + "</div>")

    cards = []
    for b in bl:
        todo = [c for c in j.get("conditions", []) if b["id"] in c.get("resolves", [])]
        steps = "".join(f'<li><span class="mono kid">{e(c["id"])}</span> {e(c["what"])}<span class="who">{e(owners[c["owner"]])}</span>'
                        f'<span class="muted small">확인: {e(c["verify"])}</span></li>' for c in todo)
        refs = "".join(f"<span class=chip>{e(x)}</span>" for x in b.get("refs", []) if x and not str(x).endswith(".json"))
        fe = "".join(f'<span class="feat">관련 기능: {e(x)}</span> ' for x in b.get("features", []))
        lvl = ("릴리즈 불가", "red") if b["level"] == "block" else ("조건부", "amber")
        cards.append(f'<div class="bmap {b["level"]}"><div class="why"><span class="flag {lvl[1]}">{lvl[0]}</span> <span class="mono muted small">{e(b["id"])} · {e(b.get("element", ""))}</span>'
                     f'<b>{e(b["title"])}</b><div>{fe}{refs}</div></div><div class="arrow" aria-hidden="true">→</div><ol class="todo">{steps}</ol></div>')
    lead = (f'릴리즈 불가 {n_block}개가 남아 있으면 배포하지 않아요. "관련 기능"이 붙은 이슈는 그 기능을 빼면 해결될 수 있어요. 조건부 {len(bl) - n_block}개는 해결하거나, 남긴 채 배포할지 QA 매니저가 정해요.'
            if n_block else f'릴리즈 불가 이슈는 없어요. 조건부 {len(bl)}개를 해결하거나, 남긴 채 배포할지 QA 매니저가 정해요.' if bl else "")
    map_html = (f'<section id="map"><h2>릴리즈 이슈 → 배포 전에 할 일 <span class="muted small">릴리즈 불가 {n_block} · 조건부 {len(bl) - n_block}</span></h2>'
                f'<p class="muted">{e(lead)}</p><div class="bmaps">{"".join(cards)}</div></section>') if bl else ""

    # 기능을 뺄 때 재회귀 범위
    shown = list(dict.fromkeys(rec.get("exclude", []) + [x for a in j.get("alternatives", []) for x in a.get("exclude", [])]))
    ex_html = []
    for x in j.get("exclusions", []):
        write_json(out / x["file"], rerun_selection(j, x))
        if x["feature"] not in shown:
            continue
        auto = sum(1 for r in x["rerun"] if r["automation"] == "api")
        rows = "".join(f'<tr><td class="mono">{e(r["id"])}</td><td>{e(r["title"])}</td><td class="c">{e(r["priority"])}</td>'
                       f'<td>{"자동화" if r["automation"] == "api" else "수동"}</td><td>{e(r["why"])}</td></tr>' for r in x["rerun"])
        keep = "".join(f'<tr><td class="mono">{e(k["id"])}</td><td>{e(k["title"])}</td><td>{e(k.get("result") or "결과 없음")}</td><td>{e(k["why"])}</td></tr>' for k in x["keep"] + x["drop"])
        ex_html.append(f'<div class="card"><h3>{e(x["feature"])} 뺀 빌드</h3><div class="split"><span>다시 돌림 <b>{len(x["rerun"])}</b>(자동화 {auto} · 수동 {len(x["rerun"]) - auto})</span>'
                       f'<span>결과 그대로 <b>{len(x["keep"])}</b></span><span>빠짐 <b>{len(x["drop"])}</b></span><span class="muted mono small">{e(x["file"])}</span></div></div>'
                       f'<div class="tbl"><table><thead><tr><th>CaseID</th><th>title</th><th class="c">우선</th><th>실행</th><th>왜 다시 돌리나</th></tr></thead><tbody>{rows}</tbody></table></div>'
                       + (f'<details><summary>다시 안 돌리는 케이스 {len(x["keep"]) + len(x["drop"])}개</summary><div class="tbl"><table><thead><tr><th>CaseID</th><th>title</th><th>이번 결과</th><th>이유</th></tr></thead><tbody>{keep}</tbody></table></div></details>' if keep else ""))
    ex_html = (f'<section id="rerun"><h2>기능을 빼면 다시 돌릴 회귀 <span class="muted small">빠진 기능 영향 + 필수(P1·지난 Fail)</span></h2>'
               f'<p class="muted">기능을 빼는 것도 코드 변경이라, 빠진 기능이 건드린 영역과 필수 케이스를 뺀 빌드로 다시 돌려요. 범위 파일은 회귀 자동화 관리자가 <span class="mono">run --selection</span>에 그대로 써요.</p>'
               + "".join(ex_html) + "</section>") if ex_html else ""

    cond_rows = "".join(
        f'<tr><td class="mono">{e(c["id"])}</td><td>{e(c["what"])}</td><td>{e(owners[c["owner"]])}</td><td>{e(c["verify"])}</td>'
        f'<td>{"".join(f"<span class=chip>{e(x)}</span>" for x in c.get("resolves", []))}</td></tr>' for c in j.get("conditions", []))
    cond_html = (f'<section id="conditions"><h2>할 일 전체</h2><div class="tbl"><table><thead><tr><th>번호</th><th>할 일</th><th>담당</th><th>확인 방법</th><th>해결하는 이슈</th></tr></thead>'
                 f'<tbody>{cond_rows}</tbody></table></div></section>') if cond_rows else ""

    el_rows = []
    for el in j["elements"]:
        ev = "".join(f"<li>{e(x)}</li>" for x in el.get("evidence", []) if x)
        note = f'<p class="note">{e(el["note"])}</p>' if el.get("note") else ""
        det = f'<details><summary>근거 {len([x for x in el.get("evidence", []) if x])}건</summary><ul class="ev">{ev}</ul></details>' if ev else ""
        el_rows.append(f'<tr><td class="mono">{e(el["id"])}</td><td><b>{e(el["name"])}</b></td><td>{pill(el["status"])}</td><td>{e(el["summary"])}{note}{det}</td></tr>')
    el_html = f'<div class="tbl"><table><thead><tr><th>요소</th><th>이름</th><th>상태</th><th>요약·근거</th></tr></thead><tbody>{"".join(el_rows)}</tbody></table></div>'

    alt = j.get("alternatives", [])
    alt_html = ""
    if alt:
        cs = "".join(f'<div class="card"><h3>{e(vl[a["verdict"]])}{e(ex_txt(a.get("exclude")))} — {e(a["option"])}</h3>'
                     f'<p><b class="good">장점</b> {e(a.get("pros", ""))}</p><p><b class="bad">단점</b> {e(a.get("cons", ""))}</p></div>' for a in alt)
        alt_html = f'<section id="alternatives"><h2>다른 선택지</h2><div class="grid">{cs}</div></section>'

    hist = ""
    if len(decisions) > 1:
        items = "".join(f'<li>{e(d["at"][:16].replace("T", " "))} · {e(vl[d["decision"]])}{e(ex_txt(d.get("exclude")))} · {e(d["by"])}{" — " + e(d["note"]) if d.get("note") else ""}</li>' for d in decisions)
        hist = f'<section id="history"><h2>결정 이력</h2><ul class="ev">{items}</ul></section>'

    css = (HERE / "templates" / "release.css").read_text(encoding="utf-8")
    title = f'{j.get("release", "")} 릴리즈 판정'
    inputs = " · ".join(f"{k}: {Path(v).name}" for k, v in j.get("inputs", {}).items())
    doc = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title><style>{css}</style></head>
<body><div class="wrap">
<header class="top"><span class="eyebrow">QA 오케스트라 · 릴리즈 판정관</span><h1>{e(title)}</h1>
<div class="meta"><span>{e(j.get("generated_at", "")[:16].replace("T", " "))}</span><span>{e(inputs)}</span></div></header>
<div class="head2">{head}{dec_html}</div>
{kpis}
{map_html}
{ex_html}
{cond_html}
<section id="elements"><h2>요소별 근거 <span class="muted small">R1~R5</span></h2>{el_html}</section>
{alt_html}
{hist}
{decision_form(j, cfg)}
<p class="muted small">릴리즈 판정관은 근거와 추천안만 낸다. 배포 여부는 사람이 정한다.</p>
</div></body></html>
"""
    (out / "release-judgment.html").write_text(doc, encoding="utf-8")

    md = [f"# {title}", "", f"**릴리즈 판정관 추천: {vl[rec['verdict']]}{ex_txt(rec.get('exclude'))}** — {rec['headline']}", ""]
    for p in rec["points"]:
        label, lines = point_parts(p)
        md += [f"- **{label}**" if label else f"- {lines.pop(0)}"] + [f"  - {x}" for x in lines]
    if rec.get("deviation_reason"):
        md += ["", f"_기준표 후보는 {vl[j['hint']]} — 다르게 추천한 이유: {rec['deviation_reason']}_"]
    md += ["", "**사람의 결정:** " + (f"{vl[last['decision']]}{ex_txt(last.get('exclude'))} ({last['by']}, {last['at'][:16].replace('T', ' ')})"
                                    + (f" — {last['note']}" if last.get("note") else "") if last else "아직 결정 전")]
    md += ["", f"회귀 범위 {cnt.get('scope', 0)}개 — Pass {cnt.get('pass', 0)} · Fail {cnt.get('fail', 0)} · 결과 없음 {cnt.get('not_run', 0)}" + (f" · 결과 시트 {sheet}" if sheet else "")]
    if bl:
        md += ["", "## 릴리즈 이슈 → 배포 전에 할 일"]
        for b in bl:
            todo = [c for c in j.get("conditions", []) if b["id"] in c.get("resolves", [])]
            md += ["", f"- **[{'릴리즈 불가' if b['level'] == 'block' else '조건부'}] {b['id']} {b['title']}**" + (f" _(관련 기능: {', '.join(b['features'])})_" if b.get("features") else "")]
            md += [f"  - {c['id']} {c['what']} ({owners[c['owner']]}) — 확인: {c['verify']}" for c in todo]
    for x in j.get("exclusions", []):
        if x["feature"] in shown:
            md += ["", f"## {x['feature']} 빼면 다시 돌릴 회귀 ({x['file']})", "",
                   f"다시 돌림 {len(x['rerun'])} · 결과 그대로 {len(x['keep'])} · 빠짐 {len(x['drop'])}", "", "| CaseID | title | 우선 | 왜 |", "|---|---|---|---|"]
            md += [f"| {r['id']} | {r['title']} | {r['priority']} | {r['why']} |" for r in x["rerun"]]
    md += ["", "## 요소별 근거", "", "| 요소 | 이름 | 상태 | 요약 |", "|---|---|---|---|"]
    md += [f"| {el['id']} | {el['name']} | {STATUS[el['status']][0]} | {el['summary']} |" for el in j["elements"]]
    for a in alt:
        md += ["", f"- 선택지 **{vl[a['verdict']]}{ex_txt(a.get('exclude'))} — {a['option']}** — 장점: {a.get('pros', '')} / 단점: {a.get('cons', '')}"]
    (out / "release-judgment.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"릴리즈 판정서: 추천 {vl[rec['verdict']]}{ex_txt(rec.get('exclude'))} · 결정 {vl[last['decision']] if last else '전'} → {out / 'release-judgment.html'}, release-judgment.md"
          + "".join(f", {x['file']}" for x in j.get("exclusions", [])))


FORM_JS = HERE / "templates" / "decision_form.js"


def decision_form(j, cfg):
    """판정서 링크(claude.ai)에서 QA 매니저가 결정을 직접 남기는 칸. 파일로 열면 안내만 보인다."""
    vl = cfg["release"]["verdicts"]
    rec = j["recommendation"]
    data = {"recommendation": rec["verdict"], "verdicts": vl, "generated_at": j.get("generated_at", ""),
            "conditions": [{"id": c["id"], "what": c["what"]} for c in j.get("conditions", [])],
            "recorded": [d["source"] for d in j.get("decisions", []) if d.get("source")]}
    radios = "".join(
        f'<label class="opt"><input type="radio" name="decision" id="dec-{k}" value="{k}"{" checked" if k == rec["verdict"] else ""}>'
        f'<span>{e(v)}{" (추천)" if k == rec["verdict"] else ""}</span></label>' for k, v in vl.items())
    feats = "".join(
        f'<label class="opt"><input type="checkbox" name="exclude" id="ex-{i}" value="{e(x["feature"])}"{" checked" if x["feature"] in rec.get("exclude", []) else ""}>'
        f'<span>{e(x["feature"])} <span class="muted small">(다시 돌림 {len(x["rerun"])}개)</span></span></label>' for i, x in enumerate(j.get("exclusions", [])))
    conds = "".join(
        f'<label class="opt"><input type="checkbox" name="cond" id="cond-{e(c["id"])}" value="{e(c["id"])}"><span><b class="mono">{e(c["id"])}</b> {e(c["what"])}</span></label>'
        for c in data["conditions"])
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f"""<section id="decide"><h2>결정 남기기</h2>
<div class="card form-card">
<p class="muted small" id="df-status">이 칸은 claude.ai 판정서 링크에서 열었을 때만 저장돼요. 파일로 열었다면 Claude에게 결정·이유를 말로 전달해 주세요.</p>
<form id="df" hidden>
<fieldset><legend>결정</legend><div class="opts">{radios}</div></fieldset>
{f'<fieldset id="df-ex"><legend>뺄 기능 (기능 빼고 Go일 때)</legend><div class="opts">{feats}</div></fieldset>' if feats else ''}
{f'<fieldset><legend>남긴 채 배포를 받아들이는 할 일 (조건부 Go일 때)</legend><div class="opts">{conds}</div></fieldset>' if conds else ''}
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
    d = {"decision": args.decision, "by": args.by, "at": now()}
    if args.note:
        d["note"] = args.note
    if args.accept:
        d["conditions_accepted"] = [x.strip() for x in args.accept.split(",") if x.strip()]
    if args.exclude:
        d["exclude"] = list(args.exclude)
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
    write_json(args.judgment, j)
    render(args.judgment, None, cfg)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--review", required=True)
    c.add_argument("--selection", required=True)
    c.add_argument("--ledger")
    c.add_argument("--analysis", nargs="*", default=[])
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
    d.add_argument("--exclude", nargs="*", default=[])
    d.add_argument("--source", help="판정서 화면에서 제출된 결정의 문서 id (같은 제출을 두 번 옮기지 않게)")
    d.add_argument("--submitted-at", dest="submitted_at")
    d.add_argument("--config")
    args = ap.parse_args()
    cfg = load_config(args.config)
    {"collect": collect, "render": lambda a, c: render(a.judgment, a.outdir, c), "decide": decide}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
