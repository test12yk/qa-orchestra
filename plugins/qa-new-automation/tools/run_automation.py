#!/usr/bin/env python3
"""신규 기능 자동화: API 테스트 스위트를 준비·점검·실행하고, 결과를 TC 시트의 결과·이슈내용 칸에 적는다.

사용법:
  python3 run_automation.py init  <스위트 폴더> [--config 설정.json] [--base-url URL]
      스위트 폴더에 qa_api.py·conftest.py·automation.json·pytest.ini 를 만든다(이미 있는 conftest.py는 그대로)
  python3 run_automation.py check <스위트 폴더> --tc <test-cases.json|시트.xlsx|.csv> [--config 설정.json]
      plan.json 이 자동화 후보 TC를 빠짐없이 다루는지, api TC마다 테스트 함수가 있는지 점검
  python3 run_automation.py run   <스위트 폴더> [--config 설정.json] [-k 표현식]
      pytest 실행 → reports/results.json, reports/run-<시각>.md
  python3 run_automation.py writeback <results.json> <TC 시트.xlsx|.csv> --out <새 시트 경로> [--config 설정.json]
      자동화한 TC 행의 결과·이슈내용 칸을 채운 시트를 새로 만든다(원본은 그대로)

표준 라이브러리만 쓴다(테스트 실행에만 pytest 필요).
"""
import base64
import csv
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape

PLUGIN = Path(__file__).resolve().parent.parent
TEMPLATES = Path(__file__).resolve().parent / "templates"
TC_NAME = re.compile(r"^test_([A-Za-z]+)_(\d+)")


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


def save(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------- init ----------

def init(suite, cfg, base_url):
    a = cfg["automation"]
    suite.mkdir(parents=True, exist_ok=True)
    (suite / "qa_api.py").write_text((TEMPLATES / "qa_api.py").read_text(encoding="utf-8"), encoding="utf-8")
    if not (suite / "conftest.py").exists():
        (suite / "conftest.py").write_text((TEMPLATES / "conftest.py").read_text(encoding="utf-8"), encoding="utf-8")
    save(suite / "automation.json", {"base_url": base_url or a.get("base_url", ""), "timeout": a.get("timeout", 10),
                                     "auth": a.get("auth", {})})
    (suite / "pytest.ini").write_text("[pytest]\naddopts = -p no:cacheprovider\njunit_family = xunit2\n", encoding="utf-8")
    print(f"✔ {suite}: qa_api.py, conftest.py, automation.json, pytest.ini")
    if not (base_url or a.get("base_url")):
        print("ℹ 서버 주소가 비어 있습니다 — automation.json의 base_url이나 환경변수 QA_BASE_URL을 채우세요.")


# ---------- TC 읽기 (시트 또는 test-cases.json) ----------

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def col_index(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def col_letter(i):
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def first_sheet(z):
    return sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))[0]


def shared_strings(z):
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    return ["".join(t.text or "" for t in si.iter(f"{NS}t"))
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{NS}si")]


def cell_value(c, shared):
    t, v = c.get("t"), c.find(f"{NS}v")
    if t == "s" and v is not None:
        return shared[int(v.text)]
    if t == "inlineStr":
        return "".join(x.text or "" for x in c.iter(f"{NS}t"))
    return v.text if v is not None and v.text else ""


def read_rows(path):
    """[(행 번호, [값…])] — 행 번호는 시트의 실제 번호(1부터)."""
    if str(path).lower().endswith(".csv"):
        with open(path, encoding="utf-8-sig", newline="") as f:
            return [(n, r) for n, r in enumerate(csv.reader(f), start=1)]
    with zipfile.ZipFile(path) as z:
        shared = shared_strings(z)
        rows = []
        for r in ET.fromstring(z.read(first_sheet(z))).iter(f"{NS}row"):
            vals = {col_index(c.get("r")): cell_value(c, shared) for c in r.findall(f"{NS}c")}
            rows.append((int(r.get("r")), [vals.get(i, "") for i in range(max(vals) + 1)] if vals else []))
        return rows


def header_map(header, cfg):
    by_label = {c["label"].strip().lower(): c["key"] for c in cfg["tc"]["columns"]}
    return {by_label[h.strip().lower()]: i for i, h in enumerate(header) if h.strip().lower() in by_label}


def candidates(tc_path, cfg):
    """자동화 후보 TC 번호. test-cases.json이면 automation 값으로, 시트면 모든 행."""
    if str(tc_path).lower().endswith(".json"):
        d = json.loads(Path(tc_path).read_text(encoding="utf-8"))
        vals = set(cfg["automation"].get("candidate_values", ["후보"]))
        return [c["id"] for c in d["cases"] if c.get("automation", next(iter(vals))) in vals]
    rows = [r for r in read_rows(tc_path) if any(str(x).strip() for x in r[1])]
    cols = header_map(rows[0][1], cfg)
    return [r[1][cols["id"]].strip() for r in rows[1:] if len(r[1]) > cols["id"] and r[1][cols["id"]].strip()]


# ---------- check ----------

def test_functions(suite):
    out = {}
    for f in sorted(suite.glob("test_*.py")):
        for m in re.finditer(r"^def (test_[A-Za-z]+_\d+\w*)\(", f.read_text(encoding="utf-8"), re.M):
            out.setdefault(tc_id(m.group(1)), []).append(f"{f.name}::{m.group(1)}")
    return out


def tc_id(test_name):
    m = TC_NAME.match(test_name)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def check(suite, tc_path, cfg):
    plan_path = suite / "plan.json"
    if not plan_path.exists():
        sys.exit(f"✘ {plan_path} 없음 — 자동화 계획을 먼저 쓰세요(형식: templates/plan.example.json).")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    modes = set(cfg["automation"]["modes"])
    errs, cands = [], candidates(tc_path, cfg)
    entries = {c.get("id"): c for c in plan.get("cases", [])}
    tests = test_functions(suite)
    errs += [f"{i}: 자동화 후보인데 plan.json에 없음" for i in cands if i not in entries]
    errs += [f"{i}: TC에 없는 번호" for i in entries if i not in cands]
    for i, c in entries.items():
        if c.get("mode") not in modes:
            errs.append(f"{i}: mode '{c.get('mode')}'는 {sorted(modes)} 중 하나여야 함")
        elif c["mode"] == "api" and i not in tests:
            errs.append(f"{i}: mode api인데 test_{i.replace('-', '_')}_… 함수가 없음")
        elif c["mode"] != "api" and not c.get("reason"):
            errs.append(f"{i}: 자동화하지 않는 이유(reason)가 없음")
        elif c["mode"] != "api" and i in tests:
            errs.append(f"{i}: mode {c['mode']}인데 테스트 함수가 있음 {tests[i]}")
    errs += [f"{i}: 테스트 함수가 있는데 plan.json에 없음 {t}" for i, t in tests.items() if i not in entries]
    if errs:
        print("✘ 점검 실패 — plan.json·테스트를 고쳐 다시 돌리세요:")
        print("\n".join(f"  - {x}" for x in errs))
        sys.exit(1)
    count = {m: sum(1 for c in entries.values() if c["mode"] == m) for m in sorted(modes)}
    print(f"✔ 점검 통과: 후보 {len(cands)}개 — " + ", ".join(f"{m} {n}" for m, n in count.items()))


# ---------- run ----------

def host_allowed(base_url, cfg):
    hosts = cfg["automation"].get("allowed_hosts", [])
    host = urlparse(base_url).hostname or ""
    return host in hosts, host, hosts


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
            return "manual", msg.replace("[자동화 제외]", "").strip()
        if kind == "fail" and msg.startswith("AssertionError"):
            body = re.sub(r"^AssertionError:\s*", "", msg)
            body = re.split(r"\nassert ", body)[0].strip()
            return "fail", body
        # AssertionError가 아닌 실패 = 테스트 코드·준비·연결 문제
        return "error", msg.splitlines()[0][:300] if msg else (el.text or "")[:300]
    return "pass", ""


RANK = {"error": 4, "fail": 3, "hold": 2, "manual": 1, "pass": 0}


def run(suite, cfg, k_expr):
    suite = suite.resolve()  # pytest는 스위트 폴더에서 돌리므로 경로를 절대 경로로
    s = json.loads((suite / "automation.json").read_text(encoding="utf-8"))
    base_url = os.environ.get("QA_BASE_URL") or s.get("base_url", "")
    ok, host, hosts = host_allowed(base_url, cfg)
    if not ok:
        sys.exit(f"✘ 실행 중단: 서버 '{host or base_url or '(비어 있음)'}'가 설정 automation.allowed_hosts {hosts}에 없습니다. "
                 "QA·스테이지 서버만 넣으세요(운영 서버 보호).")
    try:
        import pytest  # noqa: F401
    except ImportError:
        sys.exit("✘ pytest가 없습니다: python3 -m pip install pytest")
    reports = suite / "reports"
    reports.mkdir(exist_ok=True)
    junit = reports / "junit.xml"
    cmd = [sys.executable, "-m", "pytest", "-q", "-rN", f"--junitxml={junit}"] + (["-k", k_expr] if k_expr else [])
    p = subprocess.run(cmd, cwd=suite, capture_output=True, text=True)
    if not junit.exists() or p.returncode in (3, 4):
        print(p.stdout[-3000:], p.stderr[-2000:])
        sys.exit("✘ pytest 실행 실패(위 출력 참고)")
    results = {}
    for tc in ET.parse(junit).iter("testcase"):
        i = tc_id(tc.get("name", "").split("[")[0])
        if not i:
            continue
        res, msg = classify(tc)
        prev = results.get(i)
        if not prev or RANK[res] > RANK[prev["result"]]:
            results[i] = {"result": res, "message": msg, "test": f"{tc.get('classname')}::{tc.get('name')}"}
    summary = {r: sum(1 for x in results.values() if x["result"] == r) for r in RANK}
    now = datetime.now().astimezone()
    out = {"run_at": now.isoformat(timespec="seconds"), "base_url": base_url, "summary": summary,
           "results": dict(sorted(results.items()))}
    save(reports / "results.json", out)
    md = reports / f"run-{now.strftime('%Y%m%d-%H%M%S')}.md"
    names = {"pass": "Pass", "fail": "Fail", "hold": "판정 보류", "manual": "자동화 제외", "error": "자동화 오류"}
    L = [f"# 자동화 실행 결과 {now.strftime('%Y-%m-%d %H:%M')}", "", f"서버 {base_url}", "",
         " · ".join(f"{names[r]} {n}" for r, n in summary.items() if n), "", "| TC | 결과 | 내용 |", "|---|---|---|"]
    order = sorted(results.items(), key=lambda kv: (-RANK[kv[1]["result"]], kv[0]))
    for i, r in order:
        msg = r["message"].replace("\n", "<br>").replace("|", "\\|")
        L.append(f"| {i} | {names[r['result']]} | {msg} |")
    md.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("✔ 실행 끝: " + " · ".join(f"{names[r]} {n}" for r, n in summary.items() if n))
    for i, r in order:
        if r["result"] in ("fail", "error", "hold"):
            print(f"  {names[r['result']]:>6} {i}: {r['message'].splitlines()[0] if r['message'] else ''}")
    print(f"✔ {reports / 'results.json'}\n✔ {md}")


# ---------- writeback ----------

def cell_xml(ref, text, style):
    s = f' s="{style}"' if style else ""
    if text == "":
        return f'<c r="{ref}"{s}/>'
    return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


CELL = re.compile(r'<c r="([A-Z]+)(\d+)"[^>]*?(?:/>|>.*?</c>)', re.S)


def set_cells(sheet_xml, row_no, values):
    """values: {열 번호: 글자}. 행 안의 해당 칸을 바꾸거나 끼워 넣는다(열 순서 유지)."""
    m = re.search(rf'<row\b[^>]*\br="{row_no}"[^>]*>(.*?)</row>', sheet_xml, re.S)
    if not m:
        raise ValueError(f"{row_no}행을 시트에서 찾지 못함")
    inner = m.group(1)
    cells = {col_index(c.group(1)): c.group(0) for c in CELL.finditer(inner)}
    # 새 칸은 같은 행 첫 칸의 서식을 따른다
    first = re.search(r'\ss="(\d+)"', next(iter(cells.values()), ""))
    for idx, txt in values.items():
        st = re.search(r'\ss="(\d+)"', cells.get(idx, "")) or first
        cells[idx] = cell_xml(f"{col_letter(idx)}{row_no}", txt, st.group(1) if st else None)
    open_tag = re.sub(r'\sspans="[^"]*"', "", sheet_xml[m.start():m.start(1)])
    new_row = open_tag + "".join(cells[i] for i in sorted(cells)) + "</row>"
    return sheet_xml[:m.start()] + new_row + sheet_xml[m.end():]


def writeback(results_path, sheet, out, cfg):
    res = json.loads(Path(results_path).read_text(encoding="utf-8"))["results"]
    t = cfg["tc"]
    rv = t["results"]
    rows = [r for r in read_rows(sheet) if any(str(x).strip() for x in r[1])]
    cols = header_map(rows[0][1], cfg)
    missing = [k for k in ("id", "result", "issue") if k not in cols]
    if missing:
        labels = {c["key"]: c["label"] for c in t["columns"]}
        sys.exit(f"✘ 시트에 필요한 열이 없습니다: {[labels.get(k, k) for k in missing]}")
    updates = {}  # 행 번호 → (결과, 이슈내용)
    for row_no, vals in rows[1:]:
        i = vals[cols["id"]].strip() if len(vals) > cols["id"] else ""
        r = res.get(i)
        if not r or r["result"] == "manual":
            continue
        if r["result"] == "pass":
            updates[row_no] = (rv["pass"], "")
        elif r["result"] == "fail":
            updates[row_no] = (rv["fail"], "\n".join(f"- {x}" for x in r["message"].splitlines() if x.strip()))
        elif r["result"] == "hold":
            updates[row_no] = ("", f"[판정 보류] {r['message']}")
        else:
            updates[row_no] = ("", f"[자동화 오류] {r['message']}")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if str(sheet).lower().endswith(".csv"):
        with open(sheet, encoding="utf-8-sig", newline="") as f:
            data = list(csv.reader(f))
        width = max(cols["result"], cols["issue"]) + 1
        for row_no, (a, b) in updates.items():
            row = data[row_no - 1] + [""] * max(0, width - len(data[row_no - 1]))
            row[cols["result"]], row[cols["issue"]] = a, b
            data[row_no - 1] = row
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(data)
    else:
        with zipfile.ZipFile(sheet) as z:
            files = {n: z.read(n) for n in z.namelist()}
            name = first_sheet(z)
        xml = files[name].decode("utf-8")
        for row_no, (a, b) in updates.items():
            xml = set_cells(xml, row_no, {cols["result"]: a, cols["issue"]: b})
        files[name] = xml.encode("utf-8")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for n, v in files.items():
                z.writestr(n, v)
        Path(str(out) + ".b64").write_text(base64.b64encode(out.read_bytes()).decode("ascii"), encoding="ascii")
    count = {}
    for a, b in updates.values():
        k = a or b.split("]")[0] + "]"
        count[k] = count.get(k, 0) + 1
    print(f"✔ {out} — " + ", ".join(f"{k} {n}" for k, n in count.items()))


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(__doc__)
    cmd, target = args[0], Path(args[1])
    opt = {}
    for k in ("--config", "--base-url", "--tc", "--out", "-k"):
        if k in args:
            opt[k] = args[args.index(k) + 1]
    cfg = load_config(opt.get("--config"))
    if cmd == "init":
        init(target, cfg, opt.get("--base-url"))
    elif cmd == "check":
        if not opt.get("--tc"):
            sys.exit("check에는 --tc <test-cases.json|시트>가 필요합니다.")
        check(target, Path(opt["--tc"]), cfg)
    elif cmd == "run":
        run(target, cfg, opt.get("-k"))
    elif cmd == "writeback":
        if len(args) < 3 or not opt.get("--out"):
            sys.exit("writeback <results.json> <TC 시트> --out <새 시트 경로>")
        writeback(target, Path(args[2]), opt["--out"], cfg)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
