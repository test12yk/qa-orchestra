#!/usr/bin/env python3
"""QA 지휘자 도구: 파이프라인(어느 역할이 어디까지 했나)을 만들고(init), 보여 주고(status), 단계를 기록한다(done·skip·reopen).

사용법:
  python3 pipeline.py init --line new|reg|both [--feature <기능>] [--release <릴리즈>] [--root work] [--suite <suite.json>] [--no-collect]
      → <root>/pipelines/<이름>.json  (단계 목록·입력·출력 경로·사람이 할 곳)
  python3 pipeline.py status <pipeline.json>
      → 단계 표와 '지금 할 수 있는 단계', pipeline.md
  python3 pipeline.py done <pipeline.json> <단계> [--out <경로> ...] [--note "<메모>"] [--by "<사람>"]
      → 끝난 단계 기록. 출력 파일이 없으면 거부. 사람이 할 단계(human)는 --by(한 사람)와 --note(무엇을 했나·정했나)가 필요하다.
  python3 pipeline.py skip <pipeline.json> <단계> --reason "<이유>"
  python3 pipeline.py reopen <pipeline.json> <단계> --reason "<이유>"
      → 그 단계와 뒤에 이어진 단계를 다시 할 일로 돌린다(검토 보완, 기능 빼고 재회귀 등).

경로는 현재 폴더 기준이다.
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

STATUS = {"todo": "할 일", "done": "끝남", "skipped": "건너뜀"}


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def slug(s):
    return re.sub(r"[\s/\\:]+", "-", s.strip())


def step(sid, line, role, title, needs=(), outputs=(), how="", human=False, optional=False):
    return {"id": sid, "line": line, "role": role, "title": title, "needs": list(needs), "outputs": list(outputs),
            "how": how, "human": human, "optional": optional, "status": "todo", "history": []}


def new_line(f, root, collect, release):
    w = f"{root}/{slug(f)}"
    led = f"{root}/releases/{slug(release)}/features.json" if release else ""
    s = []
    if collect:
        s.append(step("N1", "new", "doc-collector", "문서 가져오기", [], [f"{w}/inputs/manifest.json"],
                      f"링크 목록 → {w}/inputs/ (연결 안 된 링크는 붙여넣기 요청)"))
    s += [
        step("N2", "new", "plan-analyst", "기획 분석", ["N1"] if collect else [], [f"{w}/analysis/plan-analysis.json"],
             f"입력 {w}/inputs/ → 출력 {w}/analysis/"),
        step("N3", "new", "new-tc-writer", "신규 TC 작성", ["N2"], [f"{w}/tc/test-cases.json"],
             f"분석서 {w}/analysis/ → 출력 {w}/tc/ (구글 시트 링크 받기)"),
        step("N4", "new", "new-reviewer", "실행 전 TC 검토", ["N3"], [f"{w}/review/tc/review.json"],
             f"--stage tc, 분석서 + {w}/tc/test-cases.json → {w}/review/tc/. 보완 필요면 N3 보완 모드 → N4 다시(reopen)"),
        step("N5", "new", "new-automation", "신규 기능 자동화 실행", ["N4"], [f"{w}/automation/reports/results.json"],
             f"TC + API 문서 + QA 서버(allowed_hosts) → {w}/automation/, 결과를 TC 시트에 기록"),
        step("N6", "new", "human", "수동 TC 실행 (테스터)", ["N5"], [],
             "자동화하지 않은 TC를 테스터가 실행하고 TC 시트 결과·이슈내용을 채운다", human=True),
        step("N7", "new", "bug-reporter", "버그 초안 → 지라 등록", ["N6"], [f"{w}/bugs"],
             f"TC 시트 Fail 행 → {w}/bugs/ 초안. 지라 등록은 사용자 승인 뒤에만"),
        step("N8", "new", "new-reviewer", "실행 결과 검토", ["N7"], [f"{w}/review/result/review.json"],
             f"--stage result → {w}/review/result/"),
        step("N9", "new", "new-judge", "신규 기능 판정", ["N8"], [f"{w}/judgment/judgment.json"],
             f"실행 결과 검토 → {w}/judgment/ (추천만)"),
        step("N10", "new", "human", "기능 편입 결정 (QA 매니저)", ["N9"], [],
             "QA 매니저가 판정서를 보고 편입 가능·조건부·보류를 정한다 → new-judge decide" + (f" --ledger {led}" if led else ""), human=True),
        step("N11", "new", "new-reporter", "신규 기능 QA 리포트", ["N10"], [f"{w}/report/report.json"],
             f"판정서 + 실행 결과 검토 → {w}/report/ (컨플루언스는 위치·제목 보여 주고 승인 뒤)"),
    ]
    return s


def reg_line(r, root, suite, features, bridge):
    g = f"{root}/regression"
    s = []
    if bridge:
        w = f"{root}/{slug(features[0])}"
        s += [
            step("R0", "reg", "reg-tc-manager", "회귀 편입 후보", ["N10"], [f"{g}/candidates/candidates.json"],
                 f"{w}/tc·review/result·judgment·analysis → 후보(편입 결정 include·conditional 뒤에만)"),
            step("R0a", "reg", "human", "회귀 편입 승인 (QA 매니저)", ["R0"], [],
                 "승인한 후보 번호만 reg-tc-manager apply --approve … --by", human=True),
        ]
    s += [
        step("R1", "reg", "reg-tc-manager", "릴리즈 회귀 범위", ["R0a"] if bridge else [], [f"{g}/release-{slug(r)}/selection.json"],
             f"스위트 {suite} + 기능 판정서(릴리즈 기록) → {g}/release-{slug(r)}/ (실행 시트 포함)"),
        step("R2", "reg", "reg-automation", "회귀 자동화 실행", ["R1"], [f"{g}/automation/reports/results.json"],
             f"status → (승인된) promote · 수동 후보 넘기기 → run --selection {g}/release-{slug(r)}/selection.json → writeback → (구글 시트) link"),
        step("R3", "reg", "human", "수동 회귀 실행 (테스터)", ["R2"], [],
             "수동 케이스를 테스터가 실행하고 회귀 실행 시트 결과·이슈내용을 채운다", human=True),
        step("R4", "reg", "reg-reviewer", "회귀 검토", ["R3"], [f"{g}/review-{slug(r)}/review.json"],
             f"results.json + 실행 시트 + 스위트 + 범위 (+ 지라 운영 결함 읽기) → {g}/review-{slug(r)}/"),
        step("R5", "reg", "release-judge", "릴리즈 판정", ["R4"], [f"{g}/release-judgment-{slug(r)}/release-judgment.json"],
             f"회귀 검토 + 범위 (+ 릴리즈 기록) → {g}/release-judgment-{slug(r)}/ (추천만)"),
        step("R6", "reg", "human", "배포 결정 (QA 매니저)", ["R5"], [],
             "QA 매니저가 Go·조건부 Go·기능 빼고 Go·No-Go를 정한다 → release-judge decide. 기능 빼고 Go면 rerun-<기능>.json으로 R2부터 다시(reopen R2)", human=True),
        step("R7", "reg", "release-reporter", "릴리즈 QA 리포트", ["R6"], [f"{g}/release-report-{slug(r)}/report.json"],
             f"릴리즈 판정서 + 회귀 검토 → {g}/release-report-{slug(r)}/ (컨플루언스는 위치·제목 보여 주고 승인 뒤)"),
    ]
    return s


def read(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def write(p, d):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_init(a):
    if a.line in ("new", "both") and not a.feature:
        sys.exit("신규 라인은 --feature <기능> 필요")
    if a.line in ("reg", "both") and not a.release:
        sys.exit("회귀 라인은 --release <릴리즈> 필요")
    suite = a.suite or f"{a.root}/regression/suite/suite.json"
    steps = []
    if a.line in ("new", "both"):
        steps += new_line(a.feature, a.root, not a.no_collect, a.release)
    if a.line in ("reg", "both"):
        steps += reg_line(a.release, a.root, suite, [a.feature] if a.feature else [], a.line == "both")
    name = slug(" ".join(x for x in (a.feature, a.release) if x))
    path = Path(a.root) / "pipelines" / f"{name}.json"
    if path.exists():
        sys.exit(f"이미 있는 파이프라인: {path} — status로 이어서 하거나 지우고 다시 만든다")
    write(path, {"name": name, "line": a.line, "feature": a.feature or "", "release": a.release or "", "suite": suite,
                 "created_at": now(), "steps": steps})
    print(f"파이프라인 만듦: {path} — 단계 {len(steps)}개 (사람이 할 곳 {sum(s['human'] for s in steps)})")
    show(path)


def ready(d):
    by = {s["id"]: s for s in d["steps"]}
    return [s for s in d["steps"] if s["status"] == "todo" and all(by[n]["status"] in ("done", "skipped") for n in s["needs"] if n in by)]


def show(path):
    d = read(path)
    rows = ["| 단계 | 역할 | 할 일 | 상태 | 메모 |", "|---|---|---|---|---|"]
    for s in d["steps"]:
        last = s["history"][-1] if s["history"] else {}
        memo = " · ".join(x for x in (last.get("by", ""), last.get("note", "") or last.get("reason", "")) if x)
        rows.append(f"| {s['id']} | {'사람' if s['human'] else s['role']} | {s['title']} | {STATUS[s['status']]} | {memo} |")
    nxt = ready(d)
    lines = [f"# 파이프라인 {d['name']}", "", *rows, ""]
    if nxt:
        by = {s["id"]: s for s in d["steps"]}

        def prev(s):
            # how의 경로는 처음 만든 기본값이다. 보완(v2) 등으로 바뀐 실제 출력은 앞 단계 기록을 쓴다.
            seen, out, stack = set(), [], list(s["needs"])
            while stack:
                n = stack.pop(0)
                if n in seen or n not in by:
                    continue
                seen.add(n)
                if by[n]["status"] == "done":
                    out += [f"{n}: {o}" for o in by[n]["outputs"]]
                else:
                    stack += by[n]["needs"]
            return out
        lines += ["**지금 할 수 있는 단계**", ""]
        for s in nxt:
            lines.append(f"- {s['id']} {s['title']} — {'사람이 할 곳: ' if s['human'] else s['role'] + ': '}{s['how']}")
            lines += [f"  - 앞 단계 실제 출력 {p}" for p in prev(s)]
    else:
        lines += ["**모든 단계 끝남**" if all(s["status"] != "todo" for s in d["steps"]) else "**앞 단계가 끝나야 진행할 수 있음**"]
    text = "\n".join(lines) + "\n"
    Path(path).with_suffix(".md").write_text(text, encoding="utf-8")
    print(text)


def find(d, sid):
    for s in d["steps"]:
        if s["id"] == sid:
            return s
    sys.exit(f"없는 단계: {sid} — 있는 단계 {', '.join(s['id'] for s in d['steps'])}")


def cmd_done(a):
    d = read(a.pipeline)
    s = find(d, a.step)
    if s not in ready(d):
        by = {x["id"]: x for x in d["steps"]}
        left = [n for n in s["needs"] if n in by and by[n]["status"] == "todo"]
        sys.exit(f"{s['id']}는 지금 할 수 없음" + (f" — 앞 단계 {', '.join(left)}가 먼저" if left else f" (상태 {STATUS[s['status']]})"))
    if s["human"] and (not a.by or not a.note):
        sys.exit(f"{s['id']} {s['title']}는 사람이 할 곳 — 한 사람(--by)과 무엇을 했나·정했나(--note)를 사람이 말한 그대로 적는다")
    outs = a.out or s["outputs"]
    miss = [o for o in outs if not Path(o).exists()]
    if miss:
        sys.exit(f"{s['id']} 출력이 없음: {', '.join(miss)} — 단계가 끝나지 않았거나 다른 위치면 --out <경로>")
    s["status"] = "done"
    s["outputs"] = outs
    s["history"].append({k: v for k, v in {"status": "done", "at": now(), "by": a.by, "note": a.note}.items() if v})
    write(a.pipeline, d)
    show(a.pipeline)


def cmd_skip(a):
    d = read(a.pipeline)
    s = find(d, a.step)
    if s["human"] and not s["optional"] and not a.by:
        sys.exit(f"{s['id']}는 사람이 할 곳 — 건너뛰려면 그렇게 정한 사람(--by)이 필요")
    s["status"] = "skipped"
    s["history"].append({k: v for k, v in {"status": "skipped", "at": now(), "reason": a.reason, "by": a.by}.items() if v})
    write(a.pipeline, d)
    show(a.pipeline)


def cmd_reopen(a):
    d = read(a.pipeline)
    start = find(d, a.step)
    todo, changed = {start["id"]}, True
    while changed:
        changed = False
        for s in d["steps"]:
            if s["id"] not in todo and any(n in todo for n in s["needs"]):
                todo.add(s["id"])
                changed = True
    for s in d["steps"]:
        if s["id"] in todo and s["status"] != "todo":
            s["status"] = "todo"
            s["history"].append({"status": "reopened", "at": now(), "reason": a.reason})
    write(a.pipeline, d)
    print(f"다시 할 일로: {', '.join(s['id'] for s in d['steps'] if s['id'] in todo)}")
    show(a.pipeline)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--line", choices=["new", "reg", "both"], required=True)
    p.add_argument("--feature")
    p.add_argument("--release")
    p.add_argument("--root", default="work")
    p.add_argument("--suite")
    p.add_argument("--no-collect", action="store_true", help="문서가 이미 inputs/에 있으면 문서 가져오기를 뺀다")
    p = sub.add_parser("status")
    p.add_argument("pipeline")
    p = sub.add_parser("done")
    p.add_argument("pipeline")
    p.add_argument("step")
    p.add_argument("--out", nargs="*")
    p.add_argument("--note")
    p.add_argument("--by")
    for name in ("skip", "reopen"):
        p = sub.add_parser(name)
        p.add_argument("pipeline")
        p.add_argument("step")
        p.add_argument("--reason", required=True)
        if name == "skip":
            p.add_argument("--by")
    a = ap.parse_args()
    if a.cmd == "status":
        show(a.pipeline)
    else:
        {"init": cmd_init, "done": cmd_done, "skip": cmd_skip, "reopen": cmd_reopen}[a.cmd](a)


if __name__ == "__main__":
    main()
