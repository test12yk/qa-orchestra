#!/usr/bin/env python3
"""버그 리포터가 쓴 bug.json을 점검하고, 리포트(MD)·지라 등록 내용을 만들고, 지라에 등록한다.

사용법:
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
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
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


def env_lines(env):
    names = {"platform": "플랫폼", "app_version": "앱/빌드 버전", "device": "기기", "os": "OS", "browser": "브라우저",
             "server": "서버 환경", "account": "테스트 계정", "network": "네트워크", "occurred_at": "발생 시각"}
    return [f"{names.get(k, k)}: {v}" for k, v in env.items() if v]


def as_list(v):
    return v if isinstance(v, list) else [v] if v else []


def render_md(b, cfg):
    L = [f"# {title(b, cfg)}", "",
         f"- 심각도: **{b['severity']}** ({cfg['bug']['severities'][b['severity']]})",
         f"- 우선순위: **{b['priority']}** ({cfg['bug']['priorities'][b['priority']]})",
         f"- 라인: {b['line']}" + (f" · 기능: {b['feature']}" if b.get("feature") else ""),
         f"- 재현율: {b.get('reproducibility', '-')}"]
    if b.get("tc_id") or b.get("req_ids"):
        L.append(f"- 연결: TC {b.get('tc_id', '-')} · 요구사항 {', '.join(b.get('req_ids', [])) or '-'}")
    L += ["", "## 환경"] + [f"- {x}" for x in env_lines(b["environment"])]
    if b.get("preconditions"):
        L += ["", "## 사전조건"] + [f"- {x}" for x in as_list(b["preconditions"])]
    L += ["", "## 재현 절차"] + [f"{i}. {s}" for i, s in enumerate(b["steps"], 1)]
    L += ["", "## 기대 결과"] + [f"- {x}" for x in as_list(b["expected"])]
    L += ["", "## 실제 결과"] + [f"- {x}" for x in as_list(b["actual"])]
    if b.get("evidence"):
        L += ["", "## 로그·근거", "```", *as_list(b["evidence"]), "```"]
    if b.get("attachments"):
        L += ["", "## 첨부"] + [f"- {x}" for x in as_list(b["attachments"])]
    if b.get("follow_up"):
        L += ["", "## 추가 확인 필요"] + [f"- {x}" for x in as_list(b["follow_up"])]
    if b.get("jira", {}).get("key"):
        L += ["", f"지라: [{b['jira']['key']}]({b['jira'].get('url', '')})"]
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
    c = [{"type": "paragraph", "content": [adf_text("심각도 ", True), adf_text(f"{b['severity']} · "), adf_text("우선순위 ", True),
                                           adf_text(f"{b['priority']} · "), adf_text("라인 ", True), adf_text(b["line"]),
                                           adf_text(f" · 재현율 {b.get('reproducibility', '-')}")]}]
    if b.get("tc_id") or b.get("req_ids"):
        c.append({"type": "paragraph", "content": [adf_text(f"연결: TC {b.get('tc_id', '-')} · 요구사항 {', '.join(b.get('req_ids', [])) or '-'}")]})
    c += [adf_heading("환경"), adf_list(env_lines(b["environment"]))]
    if b.get("preconditions"):
        c += [adf_heading("사전조건"), adf_list(as_list(b["preconditions"]))]
    c += [adf_heading("재현 절차"), adf_list(b["steps"], ordered=True),
          adf_heading("기대 결과"), adf_list(as_list(b["expected"])),
          adf_heading("실제 결과"), adf_list(as_list(b["actual"]))]
    if b.get("evidence"):
        c += [adf_heading("로그·근거"), {"type": "codeBlock", "content": [adf_text("\n".join(as_list(b["evidence"])))]}]
    if b.get("attachments"):
        c += [adf_heading("첨부"), adf_list(as_list(b["attachments"]))]
    if b.get("follow_up"):
        c += [adf_heading("추가 확인 필요"), adf_list(as_list(b["follow_up"]))]
    c.append({"type": "paragraph", "content": [adf_text("QA 오케스트라 버그 리포터가 작성 · QA 검토 후 등록")]})
    return {"type": "doc", "version": 1, "content": c}


def labels(b, cfg):
    j = cfg["jira"]
    out = list(j.get("labels", [])) + [cfg["bug"]["lines"][b["line"]]]
    if j.get("severity_field", "label") == "label":
        out.append(f"severity-{b['severity'].lower()}")
    if b.get("tc_id"):
        out.append(re.sub(r"[^\w-]", "", b["tc_id"]))
    return out


def jira_payload(b, cfg):
    j = cfg["jira"]
    fields = {"project": {"key": j["project_key"]}, "issuetype": {"name": j["issue_type"]},
              "summary": title(b, cfg)[:250], "description": to_adf(b, cfg), "labels": labels(b, cfg)}
    pr = j.get("priority_map", {}).get(b["priority"])
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
    for k in ("--config", "--key", "--url"):
        if k in args:
            opt[k] = args[args.index(k) + 1]
    cfg = load_config(opt.get("--config"))
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
            print("JIRA_PRIORITY " + str(cfg["jira"].get("priority_map", {}).get(b["priority"], "")))
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
