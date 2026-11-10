#!/usr/bin/env python3
"""신규 TC 작성가가 쓴 test-cases.json을 점검하고 스프레드시트(xlsx)·CSV·HTML·마크다운으로 낸다.

사용법: python3 render_test_cases.py <test-cases.json> [출력 폴더] [--analysis plan-analysis.json] [--config 설정.json]
출력: test-cases.xlsx(한 탭), test-cases.csv, test-cases.html, test-cases.md

점검(하나라도 걸리면 파일을 만들지 않고 종료 코드 1):
- TC 필수 필드, type·priority·technique 값, TC ID 중복
- 필수 기법(기본: 경계값·조합)이 들어 있는지
- 리그레이션 TC에 영향 기능(impact)이 적혀 있는지
- --analysis 를 주면: 모든 요구사항이 기본기능·엣지케이스 TC 또는 not_covered(이유)로 처리됐는지,
  직접 영향(direct) 기존 기능이 리그레이션 TC 또는 not_covered로 처리됐는지,
  없는 REQ·질문 번호를 참조하지 않는지
"""
import csv
import html
import json
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from xml.sax.saxutils import escape as xesc

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
CSS = (HERE / "templates" / "test_cases.css").read_text(encoding="utf-8")
REQUIRED = ["id", "domain", "section", "type", "title", "steps", "expected", "priority"]

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


def impact_names(analysis, level=None):
    return [it["name"] for g in (analysis or {}).get("impacts", []) for it in g["items"] if level is None or it.get("level") == level]


def check(d, cfg, analysis):
    t = cfg["tc"]
    errs = []
    ids = Counter(c.get("id") for c in d["cases"])
    errs += [f"TC ID 중복: {i}" for i, n in ids.items() if n > 1]
    for c in d["cases"]:
        cid = c.get("id", "?")
        miss = [k for k in REQUIRED if not c.get(k)]
        if miss:
            errs.append(f"{cid}: 필수 필드 없음 {miss}")
        if c.get("type") not in t["types"]:
            errs.append(f"{cid}: type '{c.get('type')}'는 설정에 없음 {list(t['types'])}")
        if c.get("priority") not in t["priorities"]:
            errs.append(f"{cid}: priority '{c.get('priority')}'는 설정에 없음 {list(t['priorities'])}")
        bad = [x for x in c.get("techniques", []) if x not in t["techniques"]]
        if bad:
            errs.append(f"{cid}: techniques {bad}는 설정에 없음 {t['techniques']}")
        if c.get("type") == "리그레이션" and not c.get("impact"):
            errs.append(f"{cid}: 리그레이션 TC는 impact(흔들릴 수 있는 기존 기능)를 적어야 함")
        if c.get("type") != "리그레이션" and not c.get("req_ids"):
            errs.append(f"{cid}: req_ids 없음")
    used = {x for c in d["cases"] for x in c.get("techniques", [])}
    errs += [f"필수 기법 '{x}' TC가 하나도 없음" for x in t.get("required_techniques", []) if x not in used]
    if analysis:
        req_ids = [r["id"] for r in analysis["requirements"]]
        q_ids = {q["id"] for q in analysis.get("questions", [])}
        excused = {x["req_id"] for x in d.get("not_covered", []) if x.get("reason")}
        covered = {r for c in d["cases"] if c.get("type") != "리그레이션" for r in c.get("req_ids", [])}
        errs += [f"{r}: TC도 없고 not_covered 이유도 없음" for r in req_ids if r not in covered and r not in excused]
        errs += [f"{c['id']}: 분석서에 없는 요구사항 {r}" for c in d["cases"] for r in c.get("req_ids", []) if r not in req_ids]
        errs += [f"{c['id']}: 분석서에 없는 질문 {q}" for c in d["cases"] for q in c.get("assumptions", []) if q not in q_ids]
        hit = {c.get("impact") for c in d["cases"] if c.get("type") == "리그레이션"}
        errs += [f"직접 영향 기능 '{n}': 리그레이션 TC도 없고 not_covered 이유도 없음"
                 for n in impact_names(analysis, "direct") if n not in hit and n not in excused]
        known = set(impact_names(analysis))
        errs += [f"{c['id']}: impact '{c['impact']}'는 분석서 영향 범위에 없음" for c in d["cases"]
                 if c.get("type") == "리그레이션" and known and c.get("impact") not in known]
    return errs


def title_of(c, cfg):
    tags = [x for x in cfg["tc"].get("title_tags", []) if x in c.get("techniques", [])]
    return "".join(f"[{x}]" for x in tags) + (" " if tags else "") + c["title"]


def cell(c, key, cfg):
    if key == "title":
        return title_of(c, cfg)
    if key == "steps":
        return "\n".join(f"{i}. {s}" for i, s in enumerate(c.get("steps", []), 1))
    if key == "preconditions":
        return "\n".join(f"- {x}" for x in c.get("preconditions", []))
    if key == "expected":
        lines = [f"- {x}" for x in c.get("expected", [])]
        # 미답변 질문에 기댄 케이스는 시트에서도 보이게 기대결과 끝에 붙인다
        if c.get("assumptions"):
            lines.append(f"※ 가정 {', '.join(c['assumptions'])}" + (f": {c['note']}" if c.get("note") else ""))
        return "\n".join(lines)
    v = c.get(key, "")
    return ", ".join(map(str, v)) if isinstance(v, list) else str(v)


# ---------- xlsx (표준 라이브러리만 사용) ----------

def col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "application/vnd.openxmlformats-officedocument"

# 최소 테마. 엑셀은 없어도 열지만 구글 시트 등 일부 프로그램은 테마·문서 속성이 없으면 열지 못한다.
THEME = (XML_HEAD + '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office Theme"><a:themeElements>'
         '<a:clrScheme name="Office"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
         '<a:dk2><a:srgbClr val="1F497D"/></a:dk2><a:lt2><a:srgbClr val="EEECE1"/></a:lt2><a:accent1><a:srgbClr val="4F81BD"/></a:accent1>'
         '<a:accent2><a:srgbClr val="C0504D"/></a:accent2><a:accent3><a:srgbClr val="9BBB59"/></a:accent3><a:accent4><a:srgbClr val="8064A2"/></a:accent4>'
         '<a:accent5><a:srgbClr val="4BACC6"/></a:accent5><a:accent6><a:srgbClr val="F79646"/></a:accent6><a:hlink><a:srgbClr val="0000FF"/></a:hlink>'
         '<a:folHlink><a:srgbClr val="800080"/></a:folHlink></a:clrScheme>'
         '<a:fontScheme name="Office"><a:majorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
         '<a:minorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
         '<a:fmtScheme name="Office"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
         '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
         '<a:lnStyleLst><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="25400"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
         '<a:ln w="38100"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
         '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
         '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
         '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>')


def write_xlsx(d, cfg, path):
    """엑셀이 직접 저장할 때와 같은 구성(공유 문자열·테마·문서 속성)으로 쓴다. 표준 라이브러리만 쓴다."""
    import datetime as dt
    t = cfg["tc"]
    cols = t["columns"]
    rows = [[c["label"] for c in cols]] + [[cell(tc, c["key"], cfg) for c in cols] for tc in d["cases"]]
    last = col_letter(len(cols) - 1)
    n = len(rows)

    strings, index = [], {}
    def sid(v):
        if v not in index:
            index[v] = len(strings)
            strings.append(v)
        return index[v]

    def row_xml(r, vals):
        style = 1 if r == 1 else 2
        cells = "".join(f'<c r="{col_letter(i)}{r}" s="{style}" t="s"><v>{sid(v)}</v></c>'
                        if v != "" else f'<c r="{col_letter(i)}{r}" s="{style}"/>' for i, v in enumerate(vals))
        return f'<row r="{r}">{cells}</row>'

    sheet_rows = "".join(row_xml(i + 1, r) for i, r in enumerate(rows))
    widths = "".join(f'<col min="{i+1}" max="{i+1}" width="{c.get("width", 16)}" customWidth="1"/>' for i, c in enumerate(cols))
    dv = []
    for key, values in (("type", list(t["types"])), ("priority", list(t["priorities"]))):
        idx = next((i for i, c in enumerate(cols) if c["key"] == key), None)
        if idx is not None:
            L = col_letter(idx)
            dv.append(f'<dataValidation type="list" allowBlank="1" showErrorMessage="1" sqref="{L}2:{L}{n + 200}">'
                      f'<formula1>"{xesc(",".join(values))}"</formula1></dataValidation>')
    sheet = (XML_HEAD + f'<worksheet xmlns="{NS_MAIN}" xmlns:r="{NS_REL}">'
             f'<dimension ref="A1:{last}{n}"/>'
             '<sheetViews><sheetView tabSelected="1" workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
             '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews>'
             '<sheetFormatPr defaultRowHeight="15"/>'
             f'<cols>{widths}</cols><sheetData>{sheet_rows}</sheetData>'
             f'<autoFilter ref="A1:{last}{n}"/>'
             + (f'<dataValidations count="{len(dv)}">{"".join(dv)}</dataValidations>' if dv else "")
             + '<pageMargins left="0.5" right="0.5" top="0.5" bottom="0.5" header="0.3" footer="0.3"/></worksheet>')
    sst = (XML_HEAD + f'<sst xmlns="{NS_MAIN}" count="{sum(1 for r in rows for v in r if v != "")}" uniqueCount="{len(strings)}">'
           + "".join(f'<si><t xml:space="preserve">{xesc(s)}</t></si>' for s in strings) + "</sst>")
    name = xesc(t.get("sheet_name", "TestCases")[:31])
    styles = (XML_HEAD + f'<styleSheet xmlns="{NS_MAIN}">'
              '<fonts count="2"><font><sz val="10"/><name val="Arial"/><family val="2"/></font>'
              '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Arial"/><family val="2"/></font></fonts>'
              '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FF1F3A8A"/><bgColor indexed="64"/></patternFill></fill></fills>'
              '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
              '<border><left style="thin"><color rgb="FFD0D5DD"/></left><right style="thin"><color rgb="FFD0D5DD"/></right>'
              '<top style="thin"><color rgb="FFD0D5DD"/></top><bottom style="thin"><color rgb="FFD0D5DD"/></bottom><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>'
              '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles><dxfs count="0"/><tableStyles count="0"/></styleSheet>')
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    files = {
        "[Content_Types].xml": (XML_HEAD + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                                '<Default Extension="xml" ContentType="application/xml"/>'
                                f'<Override PartName="/xl/workbook.xml" ContentType="{CT}.spreadsheetml.sheet.main+xml"/>'
                                f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{CT}.spreadsheetml.worksheet+xml"/>'
                                f'<Override PartName="/xl/theme/theme1.xml" ContentType="{CT}.theme+xml"/>'
                                f'<Override PartName="/xl/styles.xml" ContentType="{CT}.spreadsheetml.styles+xml"/>'
                                f'<Override PartName="/xl/sharedStrings.xml" ContentType="{CT}.spreadsheetml.sharedStrings+xml"/>'
                                '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                                f'<Override PartName="/docProps/app.xml" ContentType="{CT}.extended-properties+xml"/></Types>'),
        "_rels/.rels": (XML_HEAD + f'<Relationships xmlns="{NS_PKG}">'
                        f'<Relationship Id="rId1" Type="{NS_REL}/officeDocument" Target="xl/workbook.xml"/>'
                        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                        f'<Relationship Id="rId3" Type="{NS_REL}/extended-properties" Target="docProps/app.xml"/></Relationships>'),
        "docProps/app.xml": (XML_HEAD + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
                             'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>QA Orchestra</Application>'
                             '<DocSecurity>0</DocSecurity><ScaleCrop>false</ScaleCrop><AppVersion>1.0</AppVersion></Properties>'),
        "docProps/core.xml": (XML_HEAD + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                              'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                              'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                              f'<dc:title>{xesc(d["feature"]["name"])} 테스트 케이스</dc:title><dc:creator>QA Orchestra</dc:creator>'
                              f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
                              f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>'),
        "xl/workbook.xml": (XML_HEAD + f'<workbook xmlns="{NS_MAIN}" xmlns:r="{NS_REL}"><workbookPr/>'
                            '<bookViews><workbookView xWindow="0" yWindow="0" windowWidth="28800" windowHeight="16000" activeTab="0"/></bookViews>'
                            f'<sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets>'
                            f'<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">\'{name}\'!$A$1:${last}${n}</definedName></definedNames>'
                            '<calcPr calcId="191029"/></workbook>'),
        "xl/_rels/workbook.xml.rels": (XML_HEAD + f'<Relationships xmlns="{NS_PKG}">'
                                       f'<Relationship Id="rId1" Type="{NS_REL}/worksheet" Target="worksheets/sheet1.xml"/>'
                                       f'<Relationship Id="rId2" Type="{NS_REL}/theme" Target="theme/theme1.xml"/>'
                                       f'<Relationship Id="rId3" Type="{NS_REL}/styles" Target="styles.xml"/>'
                                       f'<Relationship Id="rId4" Type="{NS_REL}/sharedStrings" Target="sharedStrings.xml"/></Relationships>'),
        "xl/worksheets/sheet1.xml": sheet,
        "xl/theme/theme1.xml": THEME,
        "xl/styles.xml": styles,
        "xl/sharedStrings.xml": sst,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in files.items():
            z.writestr(k, v.encode("utf-8"))


def write_csv(d, cfg, path):
    cols = cfg["tc"]["columns"]
    # utf-8-sig: 엑셀에서 한글이 깨지지 않게 BOM을 붙인다
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([c["label"] for c in cols])
        for tc in d["cases"]:
            w.writerow([cell(tc, c["key"], cfg) for c in cols])


# ---------- HTML ----------

def coverage_rows(d, analysis):
    by_req = defaultdict(list)
    for c in d["cases"]:
        for r in c.get("req_ids", []):
            by_req[r].append(c)
    excused = {x["req_id"]: x["reason"] for x in d.get("not_covered", [])}
    if analysis:
        reqs = [(r["id"], r.get("keyword", ""), r.get("text", "")) for r in analysis["requirements"]]
    else:
        reqs = [(r, "", "") for r in sorted(by_req)]
    return reqs, by_req, excused


def prio_class(p, prios):
    i = prios.index(p) if p in prios else 9
    return "red" if i == 0 else "amber" if i == 1 else "green"


def render_html(d, cfg, analysis):
    t = cfg["tc"]
    f = d["feature"]
    cases = d["cases"]
    prios, types = list(t["priorities"]), list(t["types"])
    reqs, by_req, excused = coverage_rows(d, analysis)
    covered = sum(1 for r, _, _ in reqs if any(c["type"] != "리그레이션" for c in by_req.get(r, [])))
    pct = round(covered * 100 / len(reqs)) if reqs else 0
    top = sum(1 for c in cases if c["priority"] == prios[0])
    as_cases = [c for c in cases if c.get("assumptions")]
    tcount = Counter(c["type"] for c in cases)
    tech = Counter(x for c in cases for x in c.get("techniques", []))

    cov = []
    for rid, kw, text in reqs:
        cs = [c for c in by_req.get(rid, []) if c["type"] != "리그레이션"]
        chips = "".join(f'<span class="chip{" p0" if c["priority"] == prios[0] else ""}">{e(c["id"])} {e(c["type"])}</span>' for c in cs)
        if not cs:
            chips = f'<span class="muted">{e(excused.get(rid, "TC 없음"))}</span>'
        cov.append(f'<tr class="{"" if cs else "miss"}"><td class="mono">{e(rid)}</td><td><b>{e(kw)}</b><span class="quote">{e(text)}</span></td>'
                   f'<td class="n">{len(cs)}</td><td><div class="chips">{chips}</div></td></tr>')

    reg = defaultdict(list)
    for c in cases:
        if c["type"] == "리그레이션":
            reg[c["impact"]].append(c["id"])
    imp_rows = ""
    for g in (analysis or {}).get("impacts", []):
        for it in g["items"]:
            ids = reg.get(it["name"], [])
            lvl = "직접" if it.get("level") == "direct" else "간접"
            body = "".join(f"<span class=chip>{e(x)}</span>" for x in ids) or f'<span class="muted">{e(excused.get(it["name"], "TC 없음"))}</span>'
            imp_rows += f'<tr class="{"miss" if not ids and it.get("level") == "direct" and it["name"] not in excused else ""}"><td>{e(g["group"])}</td><td>{e(it["name"])}</td><td>{lvl}</td><td><div class="chips">{body}</div></td></tr>'
    if not imp_rows:
        imp_rows = "".join(f'<tr><td></td><td>{e(k)}</td><td></td><td><div class="chips">{"".join(f"<span class=chip>{e(x)}</span>" for x in v)}</div></td></tr>' for k, v in reg.items())

    cnt = Counter((c["priority"], c["type"]) for c in cases)
    dist = "<tr><th></th>" + "".join(f"<th>{e(x)}</th>" for x in types) + "<th>합계</th></tr>"
    for p in prios:
        row = [cnt[(p, x)] for x in types]
        dist += f'<tr><th title="{e(t["priorities"][p])}">{p}</th>' + "".join(f'<td class="{"z" if v == 0 else ""}">{v or "·"}</td>' for v in row) + f"<td><b>{sum(row)}</b></td></tr>"
    tech_chips = "".join(f'<span class="chip">{e(x)} <b>{tech[x]}</b></span>' for x in t["techniques"] if tech[x])

    qs = {q["id"]: q for q in (analysis or {}).get("questions", [])}
    by_q = defaultdict(list)
    for c in as_cases:
        for q in c["assumptions"]:
            by_q[q].append(c["id"])
    as_rows = "".join(
        f'<tr><td class="mono">{e(q)}</td><td>{e(qs.get(q, {}).get("question", ""))}<span class="quote">임시 가정: {e(qs.get(q, {}).get("assumption", ""))}</span></td>'
        f'<td>{e(qs.get(q, {}).get("ask_to", ""))}</td><td><div class="chips">{"".join(f"<span class=chip>{e(x)}</span>" for x in ids)}</div></td></tr>'
        for q, ids in sorted(by_q.items(), key=lambda x: (len(x[0]), x[0])))

    groups = defaultdict(list)
    for c in cases:
        groups[(c["domain"], c["section"])].append(c)
    tcs = []
    for (dom, sec), cs in groups.items():
        tcs.append(f'<div class="group-h">{e(dom)} › {e(sec)} <span class="muted mono">{len(cs)}</span></div>')
        for c in cs:
            pre = "".join(f"<li>{e(x)}</li>" for x in c.get("preconditions", []))
            steps = "".join(f"<li>{e(x)}</li>" for x in c["steps"])
            exp = "".join(f"<li>{e(x)}</li>" for x in c["expected"])
            flags = f'<span class="flag {prio_class(c["priority"], prios)}" title="{e(t["priorities"].get(c["priority"], ""))}">{e(c["priority"])}</span>'
            flags += f'<span class="chip">{e(c["type"])}</span>' + "".join(f'<span class="chip">{e(x)}</span>' for x in c.get("techniques", []))
            flags += "".join(f'<span class="chip as">가정 {e(q)}</span>' for q in c.get("assumptions", []))
            meta = [f"요구사항 {', '.join(c.get('req_ids', [])) or '-'}"]
            if c.get("impact"):
                meta.append(f"영향 기능 {c['impact']}")
            meta.append(f"근거 {'; '.join(c.get('refs', [])) or '-'}")
            if c.get("note"):
                meta.append(f"비고 {c['note']}")
            tcs.append(f'''<article class="tc" data-p="{e(c["priority"])}" data-t="{e(c["type"])}" data-a="{"1" if c.get("assumptions") else "0"}">
  <div class="tc-head"><span class="tid">{e(c["id"])}</span><b>{e(c["title"])}</b>{flags}</div>
  <div class="tc-body">
    <div><h4>사전조건</h4><ul>{pre or "<li class=muted>없음</li>"}</ul></div>
    <div><h4>절차</h4><ol>{steps}</ol></div>
    <div style="grid-column:1/-1"><h4>기대결과</h4><ul>{exp}</ul></div>
  </div>
  <div class="tc-meta">{"".join(f"<span>{e(m)}</span>" for m in meta)}</div>
</article>''')
    buttons = ('<button type="button" aria-pressed="true" data-f="all">전체</button>'
               + "".join(f'<button type="button" aria-pressed="false" data-f="t:{x}">{x}</button>' for x in types)
               + "".join(f'<button type="button" aria-pressed="false" data-f="p:{p}">{p}</button>' for p in prios)
               + '<button type="button" aria-pressed="false" data-f="as">가정 의존</button>')
    notes = "".join(f"<li>{e(n)}</li>" for n in d.get("notes", []))
    legend = " · ".join(f"<b>{p}</b> {e(v)}" for p, v in t["priorities"].items())

    return f"""<title>{e(f["name"])} 테스트 케이스</title>
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
    <h1>{e(f["name"])} · 테스트 케이스</h1>
    <div class="meta"><span>작성일 {e(f.get("date", ""))}</span><span>기준: {e(f.get("analysis", ""))}</span><span>스프레드시트: test-cases.xlsx</span></div>
  </header>
  <div class="kpis">
    <div class="kpi"><b>{len(cases)}</b><span>테스트 케이스 (기본 {tcount["기본기능"]} · 리그레이션 {tcount["리그레이션"]} · 엣지 {tcount["엣지케이스"]})</span></div>
    <div class="kpi{" warn" if pct < 100 else ""}"><b>{pct}%</b><span>요구사항 커버리지 ({covered}/{len(reqs)})</span></div>
    <div class="kpi warn"><b>{top}</b><span>{prios[0]} · {e(t["priorities"][prios[0]])}</span></div>
    <div class="kpi"><b>{tech["경계값"]} / {tech["조합"]}</b><span>경계값 / 조합 케이스</span></div>
    <div class="kpi caution"><b>{len(as_cases)}</b><span>가정에 기댄 TC</span></div>
  </div>
  <nav class="toc" aria-label="목차"><a href="#cov">요구사항 커버리지</a><a href="#reg">리그레이션 범위</a><a href="#dist">우선순위 × type</a><a href="#assume">가정에 기댄 TC</a><a href="#list">TC 목록</a></nav>
  <section id="cov"><h2><span class="no">1</span>요구사항 커버리지 <span class="muted" style="font-size:.8rem; font-weight:500">기본기능·엣지케이스 기준</span></h2>
    <div class="card tbl"><table class="cov"><thead><tr><th>요구사항</th><th>내용</th><th class="c">TC</th><th>케이스</th></tr></thead><tbody>{"".join(cov)}</tbody></table></div></section>
  <section id="reg"><h2><span class="no">2</span>리그레이션 범위 <span class="muted" style="font-size:.8rem; font-weight:500">이번 변경으로 흔들릴 수 있는 기존 기능</span></h2>
    <div class="card tbl"><table class="cov"><thead><tr><th>영역</th><th>기존 기능</th><th>영향</th><th>리그레이션 TC</th></tr></thead><tbody>{imp_rows}</tbody></table>
    <p class="muted" style="font-size:.8rem; margin-top:8px">회귀 스위트에 계속 넣을지는 회귀 TC 관리자가 별도 기준으로 정해요.</p></div></section>
  <section id="dist"><h2><span class="no">3</span>우선순위 × type</h2>
    <div class="card tbl"><table class="dist">{dist}</table><div class="chips" style="margin-top:10px">{tech_chips}</div>
    <p class="muted" style="font-size:.8rem; margin-top:8px">{legend}</p></div></section>
  <section id="assume"><h2><span class="no">4</span>가정에 기댄 TC <span class="muted" style="font-size:.8rem; font-weight:500">→ 답이 오면 이 TC부터 다시 본다</span></h2>
    <div class="card tbl">{f'<table><thead><tr><th>질문</th><th>내용 · 임시 가정</th><th>물어볼 사람</th><th>영향받는 TC</th></tr></thead><tbody>{as_rows}</tbody></table>' if as_rows else '<p class="muted">가정에 기댄 TC가 없어요.</p>'}</div></section>
  <section id="list"><h2><span class="no">5</span>TC 목록</h2>
    <div class="filters" role="group" aria-label="TC 거르기">{buttons}</div>
    <div class="card">{"".join(tcs)}</div>
    {f'<div class="card"><h3>메모</h3><ul>{notes}</ul></div>' if notes else ""}</section>
  <footer>QA 오케스트라 신규 TC 작성가가 만든 문서예요. 원본 데이터: test-cases.json · 스프레드시트: test-cases.xlsx</footer>
</div>
<script>
document.querySelectorAll('.filters button').forEach(b => b.addEventListener('click', () => {{
  document.querySelectorAll('.filters button').forEach(x => x.setAttribute('aria-pressed', x === b));
  const f = b.dataset.f;
  document.querySelectorAll('.tc').forEach(t => {{
    let show = f === 'all';
    if (f === 'as') show = t.dataset.a === '1';
    else if (f.startsWith('t:')) show = t.dataset.t === f.slice(2);
    else if (f.startsWith('p:')) show = t.dataset.p === f.slice(2);
    t.hidden = !show;
  }});
}}));
</script>
"""


def render_md(d, cfg, analysis):
    f = d["feature"]
    cols = cfg["tc"]["columns"]
    L = [f'# {f["name"]} · 테스트 케이스', "", f'작성일 {f.get("date", "")} · 기준: {f.get("analysis", "")}', "",
         "| " + " | ".join(c["label"] for c in cols) + " |", "|" + "---|" * len(cols)]
    for tc in d["cases"]:
        L.append("| " + " | ".join(cell(tc, c["key"], cfg).replace("\n", "<br>").replace("|", "\\|") for c in cols) + " |")
    if d.get("not_covered"):
        L += ["", "## TC를 쓰지 않은 항목"] + [f'- {x["req_id"]}: {x["reason"]}' for x in d["not_covered"]]
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
    write_xlsx(d, cfg, out / "test-cases.xlsx")
    write_csv(d, cfg, out / "test-cases.csv")
    (out / "test-cases.html").write_text(render_html(d, cfg, analysis), encoding="utf-8")
    (out / "test-cases.md").write_text(render_md(d, cfg, analysis), encoding="utf-8")
    print(f"✔ TC {len(d['cases'])}개 점검 통과")
    for n in ("xlsx", "csv", "html", "md"):
        print(f"✔ {out / ('test-cases.' + n)}")


if __name__ == "__main__":
    main()
