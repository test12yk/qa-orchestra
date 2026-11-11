#!/usr/bin/env python3
"""버그 리포터가 쓴 bug.json을 점검하고, 리포트(MD)·지라 등록 내용을 만들고, 지라에 등록한다.

사용법:
  python3 bug_report.py from-tc <TC 시트.xlsx|.csv> --out <bugs 폴더> [--line 신규기능] [--config 설정.json]
      TC 실행 시트에서 결과가 Fail인 행을 읽어 버그 초안(<id>.json)을 만든다.
      사전조건·절차·기대결과·priority는 TC 그대로, 실제 결과는 이슈내용 열에서 가져온다.
  python3 bug_report.py check  <bug.json> [--config 설정.json]
      점검 + <bug.json 폴더>/<id>.md 와 <id>.jira.json(등록 내용) 생성
  python3 bug_report.py search <bug.json> [--config 설정.json]
      (API 토큰 방식) 같은 프로젝트에서 제목이 비슷한 열린 이슈를 찾는다
  python3 bug_report.py create <bug.json> [--config 설정.json] [--dry-run]
      (API 토큰 방식) 지라에 등록하고 bug.json에 jira 키·링크를 적는다
  python3 bug_report.py record <bug.json> --key QAO-12 --url https://...
      (커넥터 방식) 커넥터로 등록한 결과를 bug.json·registry.json에 적는다

API 토큰 방식은 환경변수 JIRA_EMAIL, JIRA_API_TOKEN 을 쓴다. 토큰은 파일에 저장하지 않는다.
"""
import base64
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
REQUIRED = ["id", "summary", "line", "environment", "steps", "expected", "actual", "severity", "priority"]


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


def check(b, cfg):
    errs = [f"필수 필드 없음: {k}" for k in REQUIRED if not b.get(k)]
    if b.get("severity") and b["severity"] not in cfg["bug"]["severities"]:
        errs.append(f"severity '{b['severity']}'는 설정에 없음 {list(cfg['bug']['severities'])}")
    if b.get("priority") and b["priority"] not in cfg["bug"]["priorities"]:
        errs.append(f"priority '{b['priority']}'는 설정에 없음 {list(cfg['bug']['priorities'])}")
    j = cfg["jira"]
    if j.get("priority_from", "priority") not in ("priority", "severity"):
        errs.append(f"jira.priority_from '{j['priority_from']}'는 priority 또는 severity여야 함")
    elif j.get("project_key") and j.get("priority_map") and b.get(priority_source(cfg)) \
            and b[priority_source(cfg)] not in j["priority_map"]:
        errs.append(f"jira.priority_map에 '{b[priority_source(cfg)]}'가 없음 {list(j['priority_map'])} "
                    f"(priority_from={priority_source(cfg)})")
    if b.get("line") and b["line"] not in cfg["bug"]["lines"]:
        errs.append(f"line '{b['line']}'는 설정에 없음 {list(cfg['bug']['lines'])}")
    if isinstance(b.get("steps"), list) and len(b["steps"]) < 2:
        errs.append("재현 절차가 너무 짧음(2단계 이상)")
    if b.get("actual") and b.get("expected") and b["actual"] == b["expected"]:
        errs.append("기대 결과와 실제 결과가 같음")
    return errs


def title(b, cfg):
    env = b.get("environment", {})
    platform = env.get("platform") or "공통"
    return cfg["bug"]["title_format"].format(platform=platform, summary=b["summary"])


# 리포트 머리글·항목명 (jira.language)
TEXT = {
    "ko": {"severity": "심각도", "priority": "우선순위", "line": "라인", "feature": "기능", "repro": "재현율",
           "link": "연결", "req": "요구사항", "env": "환경", "pre": "사전조건", "steps": "재현 절차",
           "expected": "기대 결과", "actual": "실제 결과", "evidence": "로그·근거", "attach": "첨부",
           "follow": "추가 확인 필요", "footer": "QA 오케스트라 버그 리포터가 작성 · QA 검토 후 등록",
           "env_names": {"platform": "플랫폼", "app_version": "앱/빌드 버전", "device": "기기", "os": "OS",
                         "browser": "브라우저", "server": "서버 환경", "account": "테스트 계정",
                         "network": "네트워크", "occurred_at": "발생 시각"}},
    "en": {"severity": "Severity", "priority": "Priority", "line": "Line", "feature": "Feature",
           "repro": "Reproducibility", "link": "Links", "req": "Requirements", "env": "Environment",
           "pre": "Preconditions", "steps": "Steps to Reproduce", "expected": "Expected Result",
           "actual": "Actual Result", "evidence": "Logs / Evidence", "attach": "Attachments",
           "follow": "Needs Follow-up", "footer": "Written by QA Orchestra bug reporter · filed after QA review",
           "env_names": {"platform": "Platform", "app_version": "App/Build version", "device": "Device", "os": "OS",
                         "browser": "Browser", "server": "Server", "account": "Test account",
                         "network": "Network", "occurred_at": "Occurred at"}},
}


def lang(cfg):
    return cfg["jira"].get("language", "ko") if cfg["jira"].get("language") in TEXT else "ko"


def tx(cfg):
    return TEXT[lang(cfg)]


def grade(cfg, kind, value):
    """'**Blocker** (정의)' — 정의 문구는 설정(한국어)이라 영어 리포트에서는 뺀다."""
    if lang(cfg) == "en":
        return f"**{value}**"
    return f"**{value}** ({cfg['bug'][kind][value]})"


def line_name(b, cfg):
    return cfg["bug"]["lines"][b["line"]] if lang(cfg) == "en" else b["line"]


def env_lines(env, cfg):
    names = tx(cfg)["env_names"]
    return [f"{names.get(k, k)}: {v}" for k, v in env.items() if v]


def as_list(v):
    return v if isinstance(v, list) else [v] if v else []


def render_md(b, cfg):
    t = tx(cfg)
    L = [f"# {title(b, cfg)}", "",
         f"- {t['severity']}: {grade(cfg, 'severities', b['severity'])}",
         f"- {t['priority']}: {grade(cfg, 'priorities', b['priority'])}",
         f"- {t['line']}: {line_name(b, cfg)}" + (f" · {t['feature']}: {b['feature']}" if b.get("feature") else ""),
         f"- {t['repro']}: {b.get('reproducibility', '-')}"]
    if b.get("tc_id") or b.get("req_ids"):
        L.append(f"- {t['link']}: TC {b.get('tc_id', '-')} · {t['req']} {', '.join(b.get('req_ids', [])) or '-'}")
    L += ["", f"## {t['env']}"] + [f"- {x}" for x in env_lines(b["environment"], cfg)]
    if b.get("preconditions"):
        L += ["", f"## {t['pre']}"] + [f"- {x}" for x in as_list(b["preconditions"])]
    L += ["", f"## {t['steps']}"] + [f"{i}. {s}" for i, s in enumerate(b["steps"], 1)]
    L += ["", f"## {t['expected']}"] + [f"- {x}" for x in as_list(b["expected"])]
    L += ["", f"## {t['actual']}"] + [f"- {x}" for x in as_list(b["actual"])]
    if b.get("evidence"):
        L += ["", f"## {t['evidence']}", "```", *as_list(b["evidence"]), "```"]
    if b.get("attachments"):
        L += ["", f"## {t['attach']}"] + [f"- {x}" for x in as_list(b["attachments"])]
    if b.get("follow_up"):
        L += ["", f"## {t['follow']}"] + [f"- {x}" for x in as_list(b["follow_up"])]
    if b.get("jira", {}).get("key"):
        L += ["", f"Jira: [{b['jira']['key']}]({b['jira'].get('url', '')})"]
    return "\n".join(L) + "\n"


# ---------- 지라 등록 내용 ----------

def adf_text(t, bold=False):
    node = {"type": "text", "text": str(t)}
    if bold:
        node["marks"] = [{"type": "strong"}]
    return node


def adf_heading(t):
    return {"type": "heading", "attrs": {"level": 3}, "content": [adf_text(t)]}


def adf_list(items, ordered=False):
    return {"type": "orderedList" if ordered else "bulletList",
            "content": [{"type": "listItem", "content": [{"type": "paragraph", "content": [adf_text(x)]}]} for x in items]}


def to_adf(b, cfg):
    t = tx(cfg)
    c = [{"type": "paragraph", "content": [adf_text(f"{t['severity']} ", True), adf_text(f"{b['severity']} · "),
                                           adf_text(f"{t['priority']} ", True), adf_text(f"{b['priority']} · "),
                                           adf_text(f"{t['line']} ", True), adf_text(line_name(b, cfg)),
                                           adf_text(f" · {t['repro']} {b.get('reproducibility', '-')}")]}]
    if b.get("tc_id") or b.get("req_ids"):
        c.append({"type": "paragraph", "content": [adf_text(f"{t['link']}: TC {b.get('tc_id', '-')} · {t['req']} {', '.join(b.get('req_ids', [])) or '-'}")]})
    c += [adf_heading(t["env"]), adf_list(env_lines(b["environment"], cfg))]
    if b.get("preconditions"):
        c += [adf_heading(t["pre"]), adf_list(as_list(b["preconditions"]))]
    c += [adf_heading(t["steps"]), adf_list(b["steps"], ordered=True),
          adf_heading(t["expected"]), adf_list(as_list(b["expected"])),
          adf_heading(t["actual"]), adf_list(as_list(b["actual"]))]
    if b.get("evidence"):
        c += [adf_heading(t["evidence"]), {"type": "codeBlock", "content": [adf_text("\n".join(as_list(b["evidence"])))]}]
    if b.get("attachments"):
        c += [adf_heading(t["attach"]), adf_list(as_list(b["attachments"]))]
    if b.get("follow_up"):
        c += [adf_heading(t["follow"]), adf_list(as_list(b["follow_up"]))]
    c.append({"type": "paragraph", "content": [adf_text(t["footer"])]})
    return {"type": "doc", "version": 1, "content": c}


def priority_source(cfg):
    return cfg["jira"].get("priority_from", "priority")


def jira_priority(b, cfg):
    """지라 Priority 칸에 넣을 이름. priority_from이 severity면 심각도, 아니면 우선순위를 priority_map으로 바꾼다."""
    return cfg["jira"].get("priority_map", {}).get(b[priority_source(cfg)], "")


def labels(b, cfg):
    j = cfg["jira"]
    out = list(j.get("labels", [])) + [cfg["bug"]["lines"][b["line"]]]
    # 심각도가 이미 Priority 칸에 들어가면 라벨로 또 붙이지 않는다
    if j.get("severity_field", "label") == "label" and priority_source(cfg) != "severity":
        out.append(f"severity-{b['severity'].lower()}")
    if b.get("tc_id"):
        out.append(re.sub(r"[^\w-]", "", b["tc_id"]))
    return out


def jira_payload(b, cfg):
    j = cfg["jira"]
    fields = {"project": {"key": j["project_key"]}, "issuetype": {"name": j["issue_type"]},
              "summary": title(b, cfg)[:250], "description": to_adf(b, cfg), "labels": labels(b, cfg)}
    pr = jira_priority(b, cfg)
    if pr:
        fields["priority"] = {"name": pr}
    sf = j.get("severity_field", "label")
    if sf and sf != "label":
        fields[sf] = {"value": b["severity"]}
    return {"fields": fields}


# ---------- API 토큰 방식 ----------

def api(cfg, method, path, body=None):
    site = cfg["jira"]["site"].replace("https://", "").rstrip("/")
    email, token = os.environ.get("JIRA_EMAIL"), os.environ.get("JIRA_API_TOKEN")
    if not (site and email and token):
        sys.exit("API 토큰 방식에는 설정의 jira.site와 환경변수 JIRA_EMAIL, JIRA_API_TOKEN이 필요합니다.")
    req = urllib.request.Request(f"https://{site}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode())
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"지라 오류 {e.code}: {e.read().decode(errors='replace')[:800]}")


def search(b, cfg):
    words = [w for w in re.findall(r"[\w가-힣]+", b["summary"]) if len(w) > 1][:6]
    text = " ".join(words).replace('"', "")
    jql = f'project = "{cfg["jira"]["project_key"]}" AND statusCategory != Done AND summary ~ "{text}" ORDER BY created DESC'
    r = api(cfg, "GET", "/rest/api/3/search/jql?" + urllib.parse.urlencode({"jql": jql, "fields": "summary,status", "maxResults": 10}))
    return [{"key": i["key"], "summary": i["fields"]["summary"], "status": i["fields"]["status"]["name"]} for i in r.get("issues", [])]


# ---------- TC 실행 시트 → 버그 초안 ----------

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def col_index(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(path):
    """첫 번째 시트를 행 목록으로 읽는다(표준 라이브러리만). 엑셀·구글 시트에서 받은 xlsx 모두."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        sheet = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))[0]
        rows = []
        for r in ET.fromstring(z.read(sheet)).iter(f"{{{NS['m']}}}row"):
            vals = {}
            for c in r.findall("m:c", NS):
                t, v = c.get("t"), c.find("m:v", NS)
                if t == "s" and v is not None:
                    val = shared[int(v.text)]
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
                else:
                    val = v.text if v is not None else ""
                vals[col_index(c.get("r"))] = val or ""
            rows.append([vals.get(i, "") for i in range(max(vals) + 1)] if vals else [])
        return rows


def read_sheet(path):
    if str(path).lower().endswith(".csv"):
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.reader(f))
    return read_xlsx(path)


def split_cell(text, numbered=False):
    """시트 셀의 '1. …' / '- …' 줄을 항목 목록으로."""
    pat = r"^\s*\d+[.)]\s*" if numbered else r"^\s*[-•*]\s*"
    return [re.sub(pat, "", x).strip() for x in str(text).splitlines() if x.strip()]


def from_tc(sheet, out, cfg, line):
    t = cfg["tc"]
    key_of = {c["label"].strip().lower(): c["key"] for c in t["columns"]}
    rows = [r for r in read_sheet(sheet) if any(str(x).strip() for x in r)]
    if not rows:
        sys.exit("시트가 비어 있습니다.")
    keys = [key_of.get(str(h).strip().lower()) for h in rows[0]]
    need = {"id", "steps", "expected", "result", "issue"}
    if need - set(keys):
        labels = {c["key"]: c["label"] for c in t["columns"]}
        sys.exit(f"시트에 필요한 열이 없습니다: {[labels.get(k, k) for k in sorted(need - set(keys))]} "
                 f"— 설정 tc.columns의 label이 시트 머리글과 같은지 확인하세요.")
    fail = t["results"]["fail"].strip().lower()
    out.mkdir(parents=True, exist_ok=True)
    existing = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob(f"{cfg['bug']['id_prefix']}-*.json"))
                if not p.name.endswith(".jira.json")]
    seen = {(b.get("tc_id"), tuple(b.get("source", {}).get("issue", []))): b for b in existing}
    nums = [int(m.group(1)) for b in existing if (m := re.search(r"-(\d+)$", b.get("id", "")))]
    nxt = max(nums, default=0) + 1
    made, skipped = [], []
    for n, r in enumerate(rows[1:], start=2):
        row = {k: str(v).strip() for k, v in zip(keys, r) if k}
        if row.get("result", "").lower() != fail:
            continue
        actual = split_cell(row.get("issue", ""))
        dup = seen.get((row["id"], tuple(actual)))
        if dup:
            skipped.append(f"{row['id']} (행 {n}) → 이미 {dup['id']}" + (f" / {dup['jira']['key']}" if dup.get("jira") else ""))
            continue
        expected = split_cell(row.get("expected", ""))
        notes = [x for x in expected if x.startswith("※")]
        b = {"id": f"{cfg['bug']['id_prefix']}-{nxt:03d}",
             "summary": "",
             "feature": " / ".join(x for x in (row.get("domain"), row.get("section")) if x),
             "line": line, "tc_id": row["id"], "req_ids": [],
             "environment": {},
             "preconditions": split_cell(row.get("preconditions", "")),
             "steps": split_cell(row.get("steps", ""), numbered=True),
             "expected": [x for x in expected if not x.startswith("※")],
             "actual": actual,
             "severity": "", "priority": row.get("priority", ""), "reproducibility": "",
             "evidence": [], "attachments": [],
             "follow_up": [x.lstrip("※ ").strip() for x in notes],
             "source": {"sheet": Path(sheet).name, "row": n,
                        "tc_title": re.sub(r"^(\[[^\]]+\])+\s*", "", row.get("title", "")), "issue": actual}}
        save(out / f"{b['id']}.json", b)
        made.append(b)
        nxt += 1
    for b in made:
        warn = "" if b["actual"] else "  ⚠ 이슈내용 비어 있음"
        print(f"✔ {b['id']} ← {b['tc_id']} (행 {b['source']['row']}) {b['source']['tc_title'][:50]}{warn}")
    for s in skipped:
        print(f"↷ 건너뜀: {s}")
    print(f"초안 {len(made)}건 · 건너뜀 {len(skipped)}건 — summary·severity·environment를 채운 뒤 check를 돌리세요.")


def save(path, b):
    Path(path).write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def record(path, b, key, url, cfg):
    b["jira"] = {"key": key, "url": url, "created": datetime.now().astimezone().isoformat(timespec="seconds")}
    save(path, b)
    reg_path = Path(path).parent / "registry.json"
    reg = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else {"bugs": []}
    reg["bugs"] = [x for x in reg["bugs"] if x["id"] != b["id"]] + [
        {"id": b["id"], "key": key, "url": url, "tc_id": b.get("tc_id"), "line": b["line"],
         "severity": b["severity"], "priority": b["priority"], "summary": b["summary"]}]
    save(reg_path, reg)
    (Path(path).parent / f"{b['id']}.md").write_text(render_md(b, cfg), encoding="utf-8")


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(__doc__)
    cmd, path = args[0], Path(args[1])
    opt = {}
    for k in ("--config", "--key", "--url", "--out", "--line"):
        if k in args:
            opt[k] = args[args.index(k) + 1]
    cfg = load_config(opt.get("--config"))
    if cmd == "from-tc":
        if not opt.get("--out"):
            sys.exit("from-tc에는 --out <bugs 폴더>가 필요합니다.")
        line = opt.get("--line", next(iter(cfg["bug"]["lines"])))
        if line not in cfg["bug"]["lines"]:
            sys.exit(f"--line '{line}'는 설정에 없음 {list(cfg['bug']['lines'])}")
        from_tc(path, Path(opt["--out"]), cfg, line)
        return
    b = json.loads(path.read_text(encoding="utf-8"))
    errs = check(b, cfg)
    if errs:
        print("✘ 점검 실패 — bug.json을 고쳐 다시 돌리세요:")
        print("\n".join(f"  - {x}" for x in errs))
        sys.exit(1)

    if cmd == "check":
        out = path.parent
        (out / f"{b['id']}.md").write_text(render_md(b, cfg), encoding="utf-8")
        payload = jira_payload(b, cfg) if cfg["jira"].get("project_key") else None
        if payload:
            save(out / f"{b['id']}.jira.json", payload)
        print(f"✔ 점검 통과: {title(b, cfg)}")
        print(f"✔ {out / (b['id'] + '.md')}")
        if payload:
            print(f"✔ {out / (b['id'] + '.jira.json')} (등록 내용)")
            print("LABELS " + json.dumps(labels(b, cfg), ensure_ascii=False))
            print("JIRA_PRIORITY " + jira_priority(b, cfg))
        else:
            print("ℹ 설정에 jira.project_key가 없어 지라 등록 내용은 만들지 않았습니다.")
    elif cmd == "search":
        print(json.dumps(search(b, cfg), ensure_ascii=False, indent=2))
    elif cmd == "create":
        if b.get("jira", {}).get("key"):
            sys.exit(f"이미 등록됨: {b['jira']['key']} — 다시 등록하지 않습니다.")
        payload = jira_payload(b, cfg)
        if "--dry-run" in args:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        r = api(cfg, "POST", "/rest/api/3/issue", payload)
        url = f"https://{cfg['jira']['site'].replace('https://', '').rstrip('/')}/browse/{r['key']}"
        record(path, b, r["key"], url, cfg)
        print(f"✔ 등록: {r['key']} {url}")
    elif cmd == "record":
        if not (opt.get("--key") and opt.get("--url")):
            sys.exit("record에는 --key와 --url이 필요합니다.")
        record(path, b, opt["--key"], opt["--url"], cfg)
        print(f"✔ 기록: {opt['--key']} {opt['--url']}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
