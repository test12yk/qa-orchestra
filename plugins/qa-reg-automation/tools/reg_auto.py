#!/usr/bin/env python3
"""회귀 자동화 관리자 도구: 회귀 스위트의 API 자동화 테스트를 관리하고(init·status·promote·register·manual),
릴리즈 회귀 범위의 자동화 케이스를 돌려(run) 회귀 실행 시트에 결과를 적는다(writeback).

사용법:
  python3 reg_auto.py init <자동화 폴더> --suite <suite.json> [--base-url URL]
      → qa_api.py·conftest.py·automation.json·pytest.ini·registry.json (이미 있는 conftest.py·registry.json은 그대로)
  python3 reg_auto.py status <자동화 폴더> [--selection <selection.json>]
      → 스위트의 API 케이스마다 실행 가능·테스트 고치기 필요·자동화 아직 안 됨·수동으로 바꿀 후보 (status.json, status.md)
  python3 reg_auto.py promote <자동화 폴더> --ids RG-0027,RG-0028 --from <신규 기능 자동화 폴더>
      → 편입된 케이스(출처 '<기능> TC-xxx')의 신규 기능 테스트를 RG 번호로 바꿔 복사하고 등록
  python3 reg_auto.py register <자동화 폴더> --ids RG-0008,RG-0010 [--note "<고친 내용>"] [--no-code-change]
      → 직접 쓴 테스트(또는 TC가 바뀐 뒤 고친 테스트)를 지금 스위트의 TC 기준으로 등록
  python3 reg_auto.py manual <자동화 폴더> --ids RG-0013 --reason "<왜 API로 확인할 수 없나>" [--needs "<있으면 자동화되는 준비 API>"]
      → 스위트는 API인데 API로 끝까지 확인할 수 없는 케이스 = 수동으로 바꿀 후보. 회귀 TC 관리자가 스위트 표시를 수동으로 바꾼다
  python3 reg_auto.py run <자동화 폴더> --selection <selection.json>
      → 이번 범위의 자동화 케이스만 실행, Fail·자동화 오류는 다시 돌려 불안정 구분 → reports/results.json, run-<시각>.md, run.html
  python3 reg_auto.py writeback <results.json> <regression-run.xlsx> --out <새 시트.xlsx>
      → 회귀 실행 시트의 결과·이슈내용 칸을 채운 새 시트(원본은 그대로)
  python3 reg_auto.py link <results.json> --sheet-url <결과 시트 링크>
      → 결과 시트(구글 시트 등) 링크를 reports/links.json에 남기고 run.html·md를 링크와 함께 다시 그린다

공통 옵션: --config <qa-orchestra.config.json>.  표준 라이브러리만 쓴다(테스트 실행에만 pytest 필요).
"""
import argparse
import ast
import base64
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
TEMPLATES = HERE / "templates"
sys.path.insert(0, str(HERE))
import xlsx  # noqa: E402

TEST_NAME = re.compile(r"^test_([A-Za-z]+)_(\d+)")
SOURCE_TC = re.compile(r"^(.+?)\s+([A-Za-z]+-\d+)$")
SIG_KEYS = ("preconditions", "steps", "expected")
# 케이스 상태(status)
STATE = {"ready": ("실행 가능", "green"), "stale": ("테스트 고치기 필요", "red"), "todo": ("자동화 아직 안 됨", "amber"),
         "unregistered": ("등록 전", "amber"), "no_code": ("테스트 함수 없음", "red"), "to_manual": ("수동으로 바꿀 후보", "muted")}
# 실행 결과(run)
RES = {"fail": ("Fail", "red"), "error": ("자동화 오류", "red"), "flaky": ("불안정", "amber"), "stale": ("테스트 고치기 필요", "amber"),
       "hold": ("판정 보류", "amber"), "todo": ("자동화 아직 안 됨", "amber"), "to_manual": ("수동으로 확인", "muted"), "pass": ("Pass", "green"), "manual": ("수동", "muted")}
RANK = {"error": 4, "fail": 3, "hold": 2, "skip": 1, "pass": 0}
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


def ids_arg(s):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def sig(case):
    """테스트가 기준으로 삼은 TC 내용(사전조건·절차·기대 결과)의 지문."""
    body = json.dumps({k: case.get(k) or [] for k in SIG_KEYS}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]


def case_id(test_name):
    m = TEST_NAME.match(test_name)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def func_prefix(cid):
    return "test_" + cid.replace("-", "_")


# ── 폴더·스위트 ──
def registry(folder):
    p = Path(folder) / "registry.json"
    if not p.exists():
        sys.exit(f"✘ {p} 없음 — 먼저 init 하세요.")
    return read_json(p)


def suite_of(reg, override=None):
    p = Path(override or reg["suite"])
    if not p.exists():
        sys.exit(f"✘ 스위트 {p} 없음 — registry.json의 suite 경로(현재 폴더 기준)를 확인하세요.")
    st = read_json(p)
    return st, {c["id"]: c for c in st["cases"] if not c.get("retired")}


def test_functions(folder):
    out = {}
    for f in sorted(Path(folder).glob("test_*.py")):
        for m in re.finditer(r"^def (test_[A-Za-z]+_\d+\w*)\(", f.read_text(encoding="utf-8"), re.M):
            out.setdefault(case_id(m.group(1)), []).append(f"{f.name}::{m.group(1)}")
    return out


def states(folder, reg, cases):
    """API 케이스마다 상태와 설명. 그리고 스위트와 맞지 않는 테스트·등록(정리할 것)."""
    tests = test_functions(folder)
    rows = {}
    for cid, c in cases.items():
        if c.get("automation") != "api":
            continue
        r, b = reg["cases"].get(cid), reg["to_manual"].get(cid)
        src = SOURCE_TC.match(c.get("source", ""))
        if r and cid not in tests:
            rows[cid] = ("no_code", f"등록돼 있는데 {func_prefix(cid)}_… 함수가 없음")
        elif r and r["sig"] != sig(c):
            rows[cid] = ("stale", changed_text(r, c))
        elif r:
            rows[cid] = ("ready", r.get("from", ""))
        elif b:
            rows[cid] = ("to_manual", manual_text(b))
        elif cid in tests:
            rows[cid] = ("unregistered", "테스트는 있음 — 확인 뒤 register")
        elif src and src.group(1) not in ("기존 회귀 스위트", "운영 결함"):
            rows[cid] = ("todo", f"{c['source']}에서 옮기기(promote)")
        else:
            rows[cid] = ("todo", "테스트 작성 필요")
    loose = [f"{cid}: 스위트에 없거나 빠진 케이스인데 등록돼 있음" for cid in reg["cases"] if cid not in cases]
    loose += [f"{cid}: 스위트에서 수동인데 등록돼 있음" for cid in reg["cases"] if cid in cases and cases[cid].get("automation") != "api"]
    loose += [f"{cid}: 테스트 함수가 있는데 스위트에 없음 {t}" for cid, t in tests.items() if cid not in cases]
    return rows, loose, tests


def manual_text(b):
    return b["reason"] + (f" — 자동화하려면: {b['needs']}" if b.get("needs") else "")


def changed_text(r, c):
    old, new = r.get("snapshot", {}), {k: c.get(k) or [] for k in SIG_KEYS}
    labels = {"preconditions": "사전조건", "steps": "절차", "expected": "기대 결과"}
    diff = [f"{labels[k]}: {' / '.join(old.get(k, [])) or '-'} → {' / '.join(new[k]) or '-'}" for k in SIG_KEYS if old.get(k) != new[k]]
    return "테스트를 만든 뒤 TC가 바뀜 — " + ("; ".join(diff) if diff else "내용 바뀜")


def code_sig(folder, nodes):
    """테스트 함수 코드의 지문(TC가 바뀐 뒤 테스트를 정말 고쳤는지 본다)."""
    parts = []
    for n in nodes:
        fname, fn = n.split("::")
        parts.append(module_parts(Path(folder) / fname)[0][fn][0])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()[:12]


def entry(c, frm, folder, nodes, note=None):
    return {"sig": sig(c), "snapshot": {k: c.get(k) or [] for k in SIG_KEYS}, "test": ", ".join(nodes), "code": code_sig(folder, nodes),
            "from": frm, "at": now(), **({"note": note} if note else {})}


# ── init ──
def cmd_init(args, cfg):
    a = cfg["automation"]
    d = Path(args.folder)
    d.mkdir(parents=True, exist_ok=True)
    (d / "qa_api.py").write_text((TEMPLATES / "qa_api.py").read_text(encoding="utf-8"), encoding="utf-8")
    if not (d / "conftest.py").exists():
        (d / "conftest.py").write_text((TEMPLATES / "conftest.py").read_text(encoding="utf-8"), encoding="utf-8")
    write_json(d / "automation.json", {"base_url": args.base_url or a.get("base_url", ""), "timeout": a.get("timeout", 10), "auth": a.get("auth", {})})
    (d / "pytest.ini").write_text("[pytest]\naddopts = -p no:cacheprovider\njunit_family = xunit2\n", encoding="utf-8")
    if not (d / "registry.json").exists():
        if not Path(args.suite).exists():
            sys.exit(f"✘ 스위트 {args.suite} 없음")
        write_json(d / "registry.json", {"suite": args.suite, "cases": {}, "to_manual": {}, "history": []})
    print(f"✔ {d}: qa_api.py, conftest.py, automation.json, pytest.ini, registry.json")
    if not (args.base_url or a.get("base_url")):
        print("ℹ 서버 주소가 비어 있습니다 — automation.json의 base_url이나 환경변수 QA_BASE_URL을 채우세요.")


# ── status ──
def selected(sel):
    over = {o["id"]: o["category"] for o in sel.get("overrides", [])}
    return [c["id"] for c in sel["cases"] if over.get(c["id"], c["category"]) != "skip"]


def cmd_status(args, cfg):
    reg = registry(args.folder)
    st, cases = suite_of(reg, args.suite)
    rows, loose, _ = states(args.folder, reg, cases)
    scope = None
    if args.selection:
        scope = set(selected(read_json(args.selection)))
        rows = {k: v for k, v in rows.items() if k in scope}
    cnt = {k: sum(1 for s, _ in rows.values() if s == k) for k in STATE}
    out = {"at": now(), "suite": {"name": st["name"], "version": st["version"]}, "scope": args.selection,
           "counts": cnt, "cases": {k: {"state": s, "detail": t} for k, (s, t) in sorted(rows.items())}, "loose": loose}
    write_json(Path(args.folder) / "status.json", out)
    head = f"스위트 {st['name']} v{st['version']} API 케이스 {len(rows)}개" + (" (이번 범위)" if scope is not None else "")
    md = [f"# 회귀 자동화 현황", "", head + " — " + " · ".join(f"{STATE[k][0]} {n}" for k, n in cnt.items() if n), ""]
    for k in ("stale", "no_code", "unregistered", "todo", "to_manual", "ready"):
        items = [(cid, t) for cid, (s, t) in sorted(rows.items()) if s == k]
        if items:
            md += [f"## {STATE[k][0]} ({len(items)})", ""] + [f"- {cid} {cases[cid]['title']} — {t}" for cid, t in items] + [""]
    hand = [{"id": cid, "reason": manual_text(b)} for cid, b in sorted(reg["to_manual"].items()) if cases.get(cid, {}).get("automation") == "api"]
    tm = Path(args.folder) / "to-manual.json"
    if hand:
        write_json(tm, {"suite": reg["suite"], "at": now(), "cases": hand})
        md += ["## 회귀 TC 관리자에게 — 자동화 표시를 수동으로", "", f"스위트 전체에서 API로 끝까지 확인할 수 없는 케이스 {len(hand)}개(`to-manual.json`):", ""]
        md += [f"- {h['id']} {cases[h['id']]['title']} — {h['reason']}" for h in hand]
        md += ["", "```", f'reg_suite.py set-automation --suite {reg["suite"]} --list {tm} --to manual --by "<누가>"', "```", ""]
    elif tm.exists():
        tm.unlink()
    if loose:
        md += ["## 정리할 것", ""] + [f"- {x}" for x in loose] + [""]
    (Path(args.folder) / "status.md").write_text("\n".join(md), encoding="utf-8")
    print("✔ " + head + ": " + " · ".join(f"{STATE[k][0]} {n}" for k, n in cnt.items() if n))
    for k in ("stale", "no_code", "unregistered", "todo"):
        for cid, (s, t) in sorted(rows.items()):
            if s == k:
                print(f"  {STATE[k][0]:>10} {cid}: {t}")
    if hand:
        print(f"  ℹ 수동으로 바꿀 후보 {len(hand)}개 → {tm} (회귀 TC 관리자 set-automation --list)")
    for x in loose:
        print(f"  ⚠ {x}")


# ── promote ──
def module_parts(path):
    src = Path(path).read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    defs, imports = {}, []
    for node in tree.body:
        start = min([d.lineno for d in getattr(node, "decorator_list", [])] + [node.lineno])
        text = "".join(lines[start - 1:node.end_lineno]).rstrip() + "\n"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs[node.name] = (text, node)
        elif isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) for t in node.targets):
            for t in node.targets:
                defs[t.id] = (text, node)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)
    return defs, imports, tree


def names_in(node):
    out = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = node.args
        out |= {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
    return out


def closure(start, defs):
    need, todo = [], list(start)
    while todo:
        n = todo.pop(0)
        if n in defs and n not in need:
            need.append(n)
            todo += sorted(names_in(defs[n][1]))
    return need


def import_lines(imports, used):
    """쓰인 이름만 남긴 import 줄 {이름: 줄}."""
    out = {}
    for node in imports:
        for a in node.names:
            nm = (a.asname or a.name).split(".")[0]
            if nm not in used:
                continue
            alias = f"{a.name} as {a.asname}" if a.asname else a.name
            out[nm] = f"from {node.module} import {alias}" if isinstance(node, ast.ImportFrom) else f"import {alias}"
    return out


def merge_into(path, blocks, imports, header):
    """blocks: [(이름, 코드)]. 같은 이름이 다른 코드로 이미 있으면 충돌. 새 import는 마지막 import 뒤에."""
    p = Path(path)
    if not p.exists():
        body = f'"""{header}"""\n' + "".join(f"{v}\n" for v in sorted(set(imports.values()))) + "".join(f"\n\n{code}" for _, code in blocks)
        return body, []
    defs, imps, tree = module_parts(p)
    have = {(a.asname or a.name).split(".")[0] for n in imps for a in n.names}
    clash = [nm for nm, code in blocks if nm in defs and defs[nm][0] != code]
    if clash:
        return None, clash
    text = p.read_text(encoding="utf-8")
    new_imp = sorted({v for k, v in imports.items() if k not in have})
    if new_imp:
        lines = text.splitlines(keepends=True)
        at = max((n.end_lineno for n in imps), default=(tree.body[0].end_lineno if tree.body and isinstance(tree.body[0], ast.Expr) else 0))
        lines[at:at] = [x + "\n" for x in new_imp]
        text = "".join(lines)
    add = [code for nm, code in blocks if nm not in defs]
    return text.rstrip() + "\n" + "".join(f"\n\n{code}" for code in add), []


def cmd_promote(args, cfg):
    folder, src_dir = Path(args.folder), Path(getattr(args, "from"))
    reg = registry(folder)
    st, cases = suite_of(reg, args.suite)
    plan = read_json(src_dir / "plan.json")
    modes = {c["id"]: c.get("mode") for c in plan.get("cases", [])}
    res_p = src_dir / "reports" / "results.json"
    results = read_json(res_p)["results"] if res_p.exists() else {}
    src_tests = test_functions(src_dir)
    errs, jobs = [], []
    for cid in ids_arg(args.ids):
        c = cases.get(cid)
        m = SOURCE_TC.match(c.get("source", "")) if c else None
        if not c:
            errs.append(f"{cid}: 스위트에 없음")
        elif cid in reg["cases"]:
            errs.append(f"{cid}: 이미 등록됨")
        elif c.get("automation") != "api":
            errs.append(f"{cid}: 스위트에서 수동 케이스")
        elif not m:
            errs.append(f"{cid}: 출처 '{c.get('source', '')}'에 신규 TC 번호가 없음 — 직접 작성 후 register")
        else:
            tc = m.group(2)
            if plan.get("feature") and m.group(1) != plan["feature"]:
                errs.append(f"{cid}: 출처 기능 '{m.group(1)}'과 자동화 폴더의 기능 '{plan['feature']}'이 다름")
            elif modes.get(tc) != "api":
                errs.append(f"{cid}: {tc}가 신규 기능 자동화에서 api가 아님({modes.get(tc)})")
            elif results.get(tc, {}).get("result") != "pass":
                errs.append(f"{cid}: {tc}의 마지막 신규 기능 자동화 결과가 Pass가 아님({results.get(tc, {}).get('result', '결과 없음')})")
            elif len(src_tests.get(tc, [])) != 1:
                errs.append(f"{cid}: {tc} 테스트 함수가 {len(src_tests.get(tc, []))}개 — 1개여야 옮길 수 있음")
            else:
                jobs.append((cid, tc, src_tests[tc][0]))
    if errs:
        sys.exit("✘ 옮기기 실패:\n  - " + "\n  - ".join(errs))
    conf_defs, conf_imps, _ = module_parts(src_dir / "conftest.py")
    files, conf_blocks, conf_used = {}, [], set()
    for cid, tc, node_id in jobs:
        fname, fn = node_id.split("::")
        defs, imps, _ = module_parts(src_dir / fname)
        need = closure([fn], defs)
        used = set().union(*(names_in(defs[n][1]) for n in need))
        new_fn = func_prefix(cid) + fn[len(func_prefix(tc)):]
        code = re.sub(rf"\bdef {fn}\(", f"def {new_fn}(", defs[fn][0], count=1)
        f = files.setdefault(fname, {"blocks": [], "imports": {}, "from": []})
        f["blocks"] += [(new_fn, code)] + [(n, defs[n][0]) for n in need if n != fn]
        f["imports"].update(import_lines(imps, used))
        f["from"].append((cid, tc, f"{fname}::{new_fn}", node_id))
        conf_used |= used
    for n in closure(sorted(conf_used), conf_defs):
        conf_blocks.append((n, conf_defs[n][0]))
    conf_need = set().union(*(names_in(conf_defs[n][1]) for n, _ in conf_blocks)) if conf_blocks else set()
    feature = plan.get("feature", "")
    conf_text, clash = merge_into(folder / "conftest.py", conf_blocks, import_lines(conf_imps, conf_need), "")
    writes = {}
    if clash:
        errs.append(f"conftest.py: 이름이 같은데 내용이 다른 준비 코드 {clash} — 한쪽 이름을 바꿔 합친 뒤 다시")
    for fname, f in files.items():
        text, cl = merge_into(folder / fname, f["blocks"], f["imports"], f"회귀 자동화 — {feature} {fname}에서 옮긴 테스트.")
        if cl:
            errs.append(f"{fname}: 이름이 같은데 내용이 다른 도우미 {cl} — 한쪽 이름을 바꿔 합친 뒤 다시")
        writes[folder / fname] = text
    if errs:
        sys.exit("✘ 옮기기 실패(아무것도 바꾸지 않음):\n  - " + "\n  - ".join(errs))
    writes[folder / "conftest.py"] = conf_text
    for p, text in writes.items():
        p.write_text(text, encoding="utf-8")
    for f in files.values():
        for cid, tc, node, orig in f["from"]:
            reg["cases"][cid] = entry(cases[cid], f"{feature} {tc} ← {src_dir / orig}", folder, [node])
    reg["history"].append({"at": now(), "change": "promote", "ids": [j[0] for j in jobs], "from": str(src_dir)})
    write_json(folder / "registry.json", reg)
    for f in files.values():
        for cid, tc, node, _ in f["from"]:
            print(f"✔ {cid} ← {tc}: {node}")
    print("ℹ 회귀 폴더에서 한 번 돌려 확인하세요: python3 -m pytest " + " ".join(n for f in files.values() for _, _, n, _ in f["from"]))


# ── register·block ──
def cmd_register(args, cfg):
    folder = Path(args.folder)
    reg = registry(folder)
    st, cases = suite_of(reg, args.suite)
    tests = test_functions(folder)
    errs, done = [], []
    for cid in ids_arg(args.ids):
        c, r = cases.get(cid), reg["cases"].get(cid)
        if not c:
            errs.append(f"{cid}: 스위트에 없음")
        elif c.get("automation") != "api":
            errs.append(f"{cid}: 스위트에서 수동 케이스")
        elif cid not in tests:
            errs.append(f"{cid}: {func_prefix(cid)}_… 테스트 함수가 없음")
        elif r and r["sig"] == sig(c):
            errs.append(f"{cid}: 이미 등록됨(TC가 바뀌지 않음)")
        elif r and not args.note:
            errs.append(f"{cid}: TC가 바뀐 케이스 — 테스트를 무엇으로 고쳤는지 --note 필요")
        elif r and r.get("code") == code_sig(folder, tests[cid]) and not args.no_code_change:
            errs.append(f"{cid}: TC가 바뀌었는데 테스트 코드가 그대로 — 고치거나, 고칠 필요가 없으면 --no-code-change와 --note(이유)")
    if errs:
        sys.exit("✘ 등록 실패:\n  - " + "\n  - ".join(errs))
    for cid in ids_arg(args.ids):
        prev = reg["cases"].get(cid)
        reg["cases"][cid] = entry(cases[cid], prev["from"] if prev else "직접 작성", folder, tests[cid], args.note)
        reg["to_manual"].pop(cid, None)
        done.append(cid)
    reg["history"].append({"at": now(), "change": "register", "ids": done, **({"note": args.note} if args.note else {})})
    write_json(folder / "registry.json", reg)
    print(f"✔ 등록 {len(done)}개: {', '.join(done)} (스위트 v{st['version']} 기준)")


def cmd_manual(args, cfg):
    folder = Path(args.folder)
    reg = registry(folder)
    _, cases = suite_of(reg, args.suite)
    ids = ids_arg(args.ids)
    errs = [f"{cid}: 스위트에 없음" for cid in ids if cid not in cases]
    errs += [f"{cid}: 스위트에서 이미 수동" for cid in ids if cid in cases and cases[cid].get("automation") != "api"]
    errs += [f"{cid}: 등록된 테스트가 있음 — 테스트를 지우고 registry에서 뺀 뒤" for cid in ids if cid in reg["cases"]]
    if errs:
        sys.exit("✘ 실패:\n  - " + "\n  - ".join(errs))
    for cid in ids:
        reg["to_manual"][cid] = {"reason": args.reason, **({"needs": args.needs} if args.needs else {}), "at": now()}
    reg["history"].append({"at": now(), "change": "to_manual", "ids": ids, "note": manual_text(reg["to_manual"][ids[0]])})
    write_json(folder / "registry.json", reg)
    print(f"✔ 수동으로 바꿀 후보 {len(ids)}개: {', '.join(ids)} — {manual_text(reg['to_manual'][ids[0]])}")


# ── run ──
def classify(tc):
    """junit testcase → (결과, 메시지)."""
    for tag, kind in (("failure", "fail"), ("error", "error"), ("skipped", "skip")):
        el = tc.find(tag)
        if el is None:
            continue
        msg = (el.get("message") or el.text or "").strip()
        if kind == "skip":
            msg = re.sub(r"^Skipped:\s*", "", msg)
            if msg.startswith("[판정 보류]"):
                return "hold", msg[len("[판정 보류]"):].strip()
            return "skip", msg.replace("[자동화 제외]", "").strip()
        if kind == "fail" and msg.startswith("AssertionError"):
            body = re.sub(r"^AssertionError:\s*", "", msg)
            return "fail", re.split(r"\nassert ", body)[0].strip()
        # AssertionError가 아닌 실패 = 테스트 코드·준비·연결 문제
        return "error", msg.splitlines()[0][:300] if msg else (el.text or "")[:300]
    return "pass", ""


def pytest_run(folder, nodes, junit):
    if junit.exists():
        junit.unlink()
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rN", f"--junitxml={junit}", *nodes], cwd=folder, capture_output=True, text=True)
    if not junit.exists() or p.returncode in (3, 4):
        print(p.stdout[-3000:], p.stderr[-2000:])
        sys.exit("✘ pytest 실행 실패(위 출력 참고)")
    out = {}
    for tc in ET.parse(junit).iter("testcase"):
        cid = case_id(tc.get("name", "").split("[")[0])
        if not cid:
            continue
        res, msg = classify(tc)
        if cid not in out or RANK[res] > RANK[out[cid][0]]:
            out[cid] = (res, msg)
    return out


def cmd_run(args, cfg):
    folder = Path(args.folder).resolve()
    a = cfg["automation"]
    reg = registry(folder)
    sel = read_json(args.selection)
    st, cases = suite_of(reg, args.suite)
    s = read_json(folder / "automation.json")
    base_url = os.environ.get("QA_BASE_URL") or s.get("base_url", "")
    host = urlparse(base_url).hostname or ""
    if host not in a.get("allowed_hosts", []):
        sys.exit(f"✘ 실행 중단: 서버 '{host or base_url or '(비어 있음)'}'가 설정 automation.allowed_hosts {a.get('allowed_hosts', [])}에 없습니다. "
                 "QA·스테이지 서버만 넣으세요(운영 서버 보호).")
    try:
        import pytest  # noqa: F401
    except ImportError:
        sys.exit("✘ pytest가 없습니다: python3 -m pip install pytest")
    scope = [cid for cid in selected(sel) if cid in cases]
    gone = [cid for cid in selected(sel) if cid not in cases]
    rows, _, tests = states(folder, reg, cases)
    results = {}
    for cid in scope:
        if cases[cid].get("automation") != "api":
            results[cid] = {"result": "manual", "message": ""}
        elif rows[cid][0] == "stale":
            results[cid] = {"result": "stale", "message": rows[cid][1]}
        elif rows[cid][0] != "ready":
            st_, why = rows[cid]
            results[cid] = {"result": "to_manual", "message": why} if st_ == "to_manual" else {"result": "todo", "message": f"{STATE[st_][0]}: {why}"}
    ready = [cid for cid in scope if cid not in results]
    reports = folder / "reports"
    reports.mkdir(exist_ok=True)
    first = pytest_run(folder, [n for cid in ready for n in tests[cid]], reports / "junit.xml") if ready else {}
    retry = [cid for cid in ready if first.get(cid, ("error",))[0] in ("fail", "error")]
    later = {}
    for k in range(int(a.get("retry_failed", 1))):
        if not retry:
            break
        again = pytest_run(folder, [n for cid in retry for n in tests[cid]], reports / f"junit-retry{k + 1}.xml")
        for cid in retry:
            later.setdefault(cid, []).append(again.get(cid, ("error", "다시 돌린 결과 없음")))
        retry = [cid for cid in retry if again.get(cid, ("error",))[0] in ("fail", "error")]
    for cid in ready:
        res, msg = first.get(cid, ("error", "실행 결과 없음(테스트를 찾지 못함)"))
        tries = [(res, msg)] + later.get(cid, [])
        if res == "skip":
            res, msg = "error", f"회귀 테스트가 건너뜀: {msg}"
        if res in ("fail", "error") and any(r == "pass" for r, _ in tries[1:]):
            n = next(i for i, (r, _) in enumerate(tries) if r == "pass") + 1
            res, msg = "flaky", f"{n - 1}번 실패 후 {n}번째에 통과 — 1회차: {msg}"
        elif res in ("fail", "error") and len(tries) > 1 and tries[-1][1] != msg:
            msg += f"\n(다시 돌린 결과: {RES[tries[-1][0]][0]} — {tries[-1][1].splitlines()[0] if tries[-1][1] else ''})"
        results[cid] = {"result": res, "message": msg, "test": ", ".join(tests[cid]), "attempts": len(tries)}
    summary = {k: sum(1 for r in results.values() if r["result"] == k) for k in RES}
    stamp = datetime.now().astimezone()
    out = {"release": sel.get("release", ""), "run_at": stamp.isoformat(timespec="seconds"), "base_url": base_url,
           "suite": {"name": st["name"], "version": st["version"]}, "selection": args.selection, "scope": len(scope),
           "summary": summary, "missing_from_suite": gone,
           "results": {cid: dict(results[cid], title=cases[cid]["title"], domain=cases[cid].get("domain", ""), priority=cases[cid].get("priority", ""))
                       for cid in scope}}
    write_json(reports / "results.json", out)
    md = reports / f"run-{stamp.strftime('%Y%m%d-%H%M%S')}.md"
    md.write_text(run_md(out), encoding="utf-8")
    out["report_md"] = md.name
    write_json(reports / "results.json", out)
    (reports / "run.html").write_text(run_html(out), encoding="utf-8")
    print(f"✔ 회귀 자동화 실행 끝({len(ready)}개 실행): " + " · ".join(f"{RES[k][0]} {n}" for k, n in summary.items() if n))
    for cid, r in out["results"].items():
        if r["result"] in ("fail", "error", "flaky", "hold", "stale"):
            print(f"  {RES[r['result']][0]:>9} {cid}: {r['message'].splitlines()[0]}")
    if gone:
        print(f"  ⚠ 범위에 있는데 스위트에 없는 케이스: {', '.join(gone)}")
    print(f"✔ {reports / 'results.json'}\n✔ {md}\n✔ {reports / 'run.html'}")


ORDER = ("fail", "error", "flaky", "hold", "stale", "todo", "to_manual", "pass", "manual")


def run_md(d):
    s = d["summary"]
    L = [f"# {d['release']} 회귀 자동화 결과", "", f"{d['run_at'][:16].replace('T', ' ')} · 서버 {d['base_url']} · 스위트 {d['suite']['name']} v{d['suite']['version']}", "",
         *([f"결과 시트: {d['sheet_url']}", ""] if d.get("sheet_url") else []),
         f"범위 {d['scope']}개 — " + " · ".join(f"{RES[k][0]} {s[k]}" for k in ORDER if s.get(k)), ""]
    for k in ORDER:
        items = [(cid, r) for cid, r in d["results"].items() if r["result"] == k]
        if not items:
            continue
        L += [f"## {RES[k][0]} ({len(items)})", ""]
        L += [f"- {cid} {r['title']}" + (f" — {r['message'].splitlines()[0]}" if r["message"] and k != "pass" else "") for cid, r in items] + [""]
    return "\n".join(L)


def run_html(d):
    s = d["summary"]
    auto = sum(s[k] for k in ("pass", "fail", "error", "flaky", "hold"))
    bad = s["fail"] + s["error"]
    banner = (f'<div class="banner warn"><b>Fail {s["fail"]} · 자동화 오류 {s["error"]}</b><span>Fail은 회귀 검토자가 원인을 본다. 자동화 오류는 테스트·준비 문제라 회귀 자동화 관리자가 고친다.</span></div>'
              if bad else '<div class="banner ok"><b>실행한 자동화 케이스에 Fail 없음</b><span>불안정·고치기 필요·자동화 아직 안 됨·수동으로 확인은 아래에서 따로 본다.</span></div>')
    url = d.get("sheet_url")

    def box(cls, b, label):
        # 결과 시트가 있으면 숫자 칸에서 바로 연다
        if url:
            return f'<a class="kpi link{cls}" href="{e(url)}" target="_blank" rel="noopener"><b>{b}</b><span>{label} ↗</span></a>'
        return f'<div class="kpi{cls}"><b>{b}</b><span>{label}</span></div>'
    kpi = ('<div class="kpis">' + box("", f"{auto}/{d['scope']}", "자동화로 실행")
           + "".join(box(" warn" if k in ("fail", "error") and s[k] else (" caution" if k in ("flaky", "stale", "hold") and s[k] else ""), s[k], RES[k][0])
                     for k in ORDER if s.get(k) or k in ("pass", "fail"))
           + "</div>")
    note = {"fail": "기대 결과와 다름 — 회귀 검토자에게", "error": "테스트·준비·연결 문제 — 회귀 자동화 관리자가 고친다",
            "flaky": "다시 돌려서 통과 — Pass로 치지 않고 사람이 확인", "hold": "TC가 정한 판정 보류 조건",
            "stale": "TC가 바뀌어 실행하지 않음 — 테스트를 고친 뒤 다시 등록", "todo": "자동화할 수 있지만 테스트가 아직 없음 — 회귀 자동화 관리자가 쓰거나 옮긴다. 이번엔 사람이 확인",
            "to_manual": "API로 끝까지 확인할 수 없음 — 이번엔 테스터가 수동으로. 스위트 자동화 표시는 회귀 TC 관리자가 수동으로 바꾼다",
            "pass": "", "manual": "수동 케이스 — 테스터가 실행"}
    sec = []
    for k in ORDER:
        items = [(cid, r) for cid, r in d["results"].items() if r["result"] == k]
        if not items:
            continue
        rows = "".join(f'<tr><td class="mono">{e(cid)}</td><td>{e(r["domain"])}</td><td>{e(r["title"])}'
                       + (f'<div class="muted small pre">{e(r["message"])}</div>' if r["message"] and k != "pass" else "")
                       + f'</td><td class="c">{e(r["priority"])}</td></tr>' for cid, r in items)
        tbl = f'<div class="tbl"><table><thead><tr><th>CaseID</th><th>Domain</th><th>title·내용</th><th class="c">우선</th></tr></thead><tbody>{rows}</tbody></table></div>'
        head = (f'<span class="flag {RES[k][1]}">{RES[k][0]}</span> {len(items)}개' + (f' <span class="muted small">{e(note[k])}</span>' if note[k] else "")
                + (f' <a class="small" href="{e(url)}" target="_blank" rel="noopener">결과 시트 ↗</a>' if url and k not in ("pass", "manual") else ""))
        sec.append(f'<section><details><summary>{head}</summary>{tbl}</details></section>' if k in ("pass", "manual") else f"<section><h2>{head}</h2>{tbl}</section>")
    meta = (f'<span>{e(d["run_at"][:16].replace("T", " "))}</span><span>서버 {e(d["base_url"])}</span>'
            f'<span>스위트 {e(d["suite"]["name"])} v{d["suite"]["version"]}</span>'
            + (f'<span><a href="{e(url)}" target="_blank" rel="noopener">회귀 실행 결과 시트 ↗</a></span>' if url else ""))
    return page(f'{d["release"]} 회귀 자동화 결과', "회귀 자동화 관리자", banner + kpi + "".join(sec), meta)


def page(title, eyebrow, body, meta=""):
    css = (TEMPLATES / "reg.css").read_text(encoding="utf-8")
    return (f'<!doctype html>\n<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{e(title)}</title><style>{css}</style></head>\n<body><div class=\"wrap\">"
            f'<header class="top"><span class="eyebrow">QA 오케스트라 · {e(eyebrow)}</span><h1>{e(title)}</h1><div class="meta">{meta}</div></header>'
            f"{body}</div></body></html>\n")


# ── writeback ──
def cmd_writeback(args, cfg):
    d = read_json(args.results)
    res = d["results"]
    s = cfg["suite"]
    rv = s["results"]
    by_label = {c["label"].strip().lower(): c for c in s["columns"]}
    sheets, count, seen = [], {}, set()
    for name, rows in xlsx.read(args.sheet):
        if not rows:
            continue
        header = [str(h) for h in rows[0]]
        cols = {by_label[h.strip().lower()]["key"]: i for i, h in enumerate(header) if h.strip().lower() in by_label}
        if not all(k in cols for k in ("id", "result", "issue")):
            sheets.append({"name": name, "header": header, "rows": [list(r) for r in rows[1:]]})
            continue
        body = []
        for r in rows[1:]:
            r = list(r) + [""] * (len(header) - len(r))
            r = r[:len(header)]
            cid = str(r[cols["id"]]).strip()
            x = res.get(cid)
            if x and x["result"] != "manual":
                cell = {"pass": (rv["pass"], ""), "fail": (rv["fail"], "\n".join(f"- {t}" for t in x["message"].splitlines() if t.strip())),
                        "flaky": ("", f"[불안정] {x['message']}"), "hold": ("", f"[판정 보류] {x['message']}"),
                        "error": ("", f"[자동화 오류] {x['message']}"), "stale": ("", f"[테스트 고치기 필요] {x['message']} — 사람이 확인"),
                        "todo": ("", f"[자동화 아직 안 됨] {x['message']} — 사람이 확인"),
                        "to_manual": ("", f"[수동으로 확인] {x['message']}")}[x["result"]]
                r[cols["result"]], r[cols["issue"]] = cell
                count[RES[x["result"]][0]] = count.get(RES[x["result"]][0], 0) + 1
                seen.add(cid)
            body.append(r)
        lists = {i: v for i, v in ((cols.get("result"), list(rv.values())), (cols.get("automation"), list(s["automation"].values()))) if i is not None}
        sheets.append({"name": name, "header": header, "rows": body, "lists": lists,
                       "widths": [by_label.get(h.strip().lower(), {}).get("width", 16) for h in header]})
    missing = [cid for cid, x in res.items() if x["result"] != "manual" and cid not in seen]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    xlsx.write(out, sheets, title=f"{d.get('release', '')} 회귀 실행 결과")
    Path(str(out) + ".b64").write_text(base64.b64encode(out.read_bytes()).decode("ascii"), encoding="ascii")
    print(f"✔ {out} — " + ", ".join(f"{k} {n}" for k, n in count.items()))
    if missing:
        print(f"  ⚠ 시트에서 못 찾은 케이스: {', '.join(missing)} — 범위 파일과 같은 실행 시트인지 확인")


def cmd_link(args, cfg):
    url = args.sheet_url.strip()
    if not re.match(r"https?://", url):
        sys.exit("✘ 링크는 http(s) 주소여야 합니다.")
    p = Path(args.results)
    d = read_json(p)
    d["sheet_url"] = url
    write_json(p, d)
    links = p.parent / "links.json"
    old = read_json(links) if links.exists() else {}
    write_json(links, {**old, "sheet": url, "run_at": d["run_at"], "at": now()})
    (p.parent / "run.html").write_text(run_html(d), encoding="utf-8")
    if d.get("report_md"):
        (p.parent / d["report_md"]).write_text(run_md(d), encoding="utf-8")
    print(f"✔ 결과 시트 링크 → {links}, run.html 다시 그림")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, *pos):
        p = sub.add_parser(name)
        for x in pos:
            p.add_argument(x)
        p.add_argument("--config", dest="config_sub")
        p.add_argument("--suite", help="registry.json의 스위트 경로 대신")
        return p

    p = add("init", "folder"); p.add_argument("--base-url", dest="base_url")
    p = add("status", "folder"); p.add_argument("--selection")
    p = add("promote", "folder"); p.add_argument("--ids", required=True); p.add_argument("--from", required=True)
    p = add("register", "folder"); p.add_argument("--ids", required=True); p.add_argument("--note")
    p.add_argument("--no-code-change", dest="no_code_change", action="store_true")
    p = add("manual", "folder"); p.add_argument("--ids", required=True); p.add_argument("--reason", required=True); p.add_argument("--needs")
    p = add("run", "folder"); p.add_argument("--selection", required=True)
    p = add("writeback", "results", "sheet"); p.add_argument("--out", required=True)
    p = add("link", "results"); p.add_argument("--sheet-url", dest="sheet_url", required=True)
    args = ap.parse_args()
    cfg = load_config(args.config_sub or args.config)
    if args.cmd == "init" and not args.suite:
        sys.exit("init에는 --suite <suite.json>이 필요합니다.")
    {"init": cmd_init, "status": cmd_status, "promote": cmd_promote, "register": cmd_register, "manual": cmd_manual,
     "run": cmd_run, "writeback": cmd_writeback, "link": cmd_link}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
