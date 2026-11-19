"""여러 탭짜리 xlsx 쓰기·읽기. 표준 라이브러리만 쓴다(엑셀·구글 시트에서 열리는 최소 구성)."""
import datetime as dt
import re
import zipfile
from xml.etree import ElementTree as ET

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

STYLES = (XML_HEAD + f'<styleSheet xmlns="{NS_MAIN}">'
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


def xesc(s):
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(s))
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def sheet_name(name, used):
    """탭 이름: 엑셀 금지 문자 제거, 31자, 중복이면 번호."""
    base = re.sub(r"[\[\]:*?/\\]", "·", name)[:31] or "Sheet"
    n, out = 2, base
    while out in used:
        out = f"{base[:28]}({n})"
        n += 1
    used.add(out)
    return out


def write(path, sheets, title="QA Orchestra"):
    """sheets: [{"name", "header": [..], "rows": [[..]], "widths": [..], "lists": {열 번호: [허용값]}}]"""
    strings, index = [], {}

    def sid(v):
        if v not in index:
            index[v] = len(strings)
            strings.append(v)
        return index[v]

    def row_xml(r, vals):
        style = 1 if r == 1 else 2
        cells = "".join(f'<c r="{col_letter(i)}{r}" s="{style}" t="s"><v>{sid(str(v))}</v></c>'
                        if v not in ("", None) else f'<c r="{col_letter(i)}{r}" s="{style}"/>' for i, v in enumerate(vals))
        return f'<row r="{r}">{cells}</row>'

    used, parts, names, total = set(), {}, [], 0
    for k, sh in enumerate(sheets, 1):
        rows = [sh["header"]] + sh["rows"]
        total += sum(1 for r in rows for v in r if v not in ("", None))
        last, n = col_letter(len(sh["header"]) - 1), len(rows)
        widths = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(sh.get("widths", [])))
        dv = "".join(f'<dataValidation type="list" allowBlank="1" showErrorMessage="1" sqref="{col_letter(i)}2:{col_letter(i)}{n + 200}">'
                     f'<formula1>"{xesc(",".join(vals))}"</formula1></dataValidation>' for i, vals in sh.get("lists", {}).items() if vals)
        sel = 'tabSelected="1" ' if k == 1 else ""
        parts[f"xl/worksheets/sheet{k}.xml"] = (
            XML_HEAD + f'<worksheet xmlns="{NS_MAIN}" xmlns:r="{NS_REL}"><dimension ref="A1:{last}{n}"/>'
            f'<sheetViews><sheetView {sel}workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="15"/>'
            + (f"<cols>{widths}</cols>" if widths else "")
            + f'<sheetData>{"".join(row_xml(i + 1, r) for i, r in enumerate(rows))}</sheetData><autoFilter ref="A1:{last}{n}"/>'
            + (f'<dataValidations count="{dv.count("<dataValidation ")}">{dv}</dataValidations>' if dv else "")
            + '<pageMargins left="0.5" right="0.5" top="0.5" bottom="0.5" header="0.3" footer="0.3"/></worksheet>')
        names.append((sheet_name(sh["name"], used), last, n))

    sst = (XML_HEAD + f'<sst xmlns="{NS_MAIN}" count="{total}" uniqueCount="{len(strings)}">'
           + "".join(f'<si><t xml:space="preserve">{xesc(s)}</t></si>' for s in strings) + "</sst>")
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    k = len(sheets)
    files = {
        "[Content_Types].xml": (XML_HEAD + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                                '<Default Extension="xml" ContentType="application/xml"/>'
                                f'<Override PartName="/xl/workbook.xml" ContentType="{CT}.spreadsheetml.sheet.main+xml"/>'
                                + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="{CT}.spreadsheetml.worksheet+xml"/>' for i in range(1, k + 1))
                                + f'<Override PartName="/xl/theme/theme1.xml" ContentType="{CT}.theme+xml"/>'
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
                              f'<dc:title>{xesc(title)}</dc:title><dc:creator>QA Orchestra</dc:creator>'
                              f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
                              f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>'),
        "xl/workbook.xml": (XML_HEAD + f'<workbook xmlns="{NS_MAIN}" xmlns:r="{NS_REL}"><workbookPr/>'
                            '<bookViews><workbookView xWindow="0" yWindow="0" windowWidth="28800" windowHeight="16000" activeTab="0"/></bookViews><sheets>'
                            + "".join(f'<sheet name="{xesc(nm)}" sheetId="{i}" r:id="rId{i}"/>' for i, (nm, _, _) in enumerate(names, 1))
                            + "</sheets><definedNames>"
                            + "".join(f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">\'{xesc(nm)}\'!$A$1:${last}${n}</definedName>'
                                      for i, (nm, last, n) in enumerate(names))
                            + '</definedNames><calcPr calcId="191029"/></workbook>'),
        "xl/_rels/workbook.xml.rels": (XML_HEAD + f'<Relationships xmlns="{NS_PKG}">'
                                       + "".join(f'<Relationship Id="rId{i}" Type="{NS_REL}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, k + 1))
                                       + f'<Relationship Id="rId{k + 1}" Type="{NS_REL}/theme" Target="theme/theme1.xml"/>'
                                       f'<Relationship Id="rId{k + 2}" Type="{NS_REL}/styles" Target="styles.xml"/>'
                                       f'<Relationship Id="rId{k + 3}" Type="{NS_REL}/sharedStrings" Target="sharedStrings.xml"/></Relationships>'),
        "xl/theme/theme1.xml": THEME,
        "xl/styles.xml": STYLES,
        "xl/sharedStrings.xml": sst,
        **parts,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in files.items():
            z.writestr(name, body.encode("utf-8"))


def read(path):
    """xlsx의 모든 탭을 [(탭 이름, [[셀 값..]..])]로 읽는다. 첫 줄은 머리글."""
    m = {"m": NS_MAIN, "r": NS_REL}
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", m):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS_MAIN}}}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = {r.get("Id"): r.get("Target") for r in rels}
        out = []
        for sh in wb.find("m:sheets", m):
            t = target[sh.get(f"{{{NS_REL}}}id")].lstrip("/")
            t = t if t.startswith("xl/") else "xl/" + t
            rows = []
            for row in ET.fromstring(z.read(t)).iter(f"{{{NS_MAIN}}}row"):
                vals = {}
                for c in row.findall("m:c", m):
                    ref = re.match(r"([A-Z]+)", c.get("r", "")).group(1)
                    col = 0
                    for ch in ref:
                        col = col * 26 + ord(ch) - 64
                    v = c.find("m:v", m)
                    if c.get("t") == "s" and v is not None:
                        val = shared[int(v.text)]
                    elif c.get("t") == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(f"{{{NS_MAIN}}}t"))
                    else:
                        val = v.text if v is not None else ""
                    vals[col - 1] = val or ""
                if vals:
                    rows.append([vals.get(i, "") for i in range(max(vals) + 1)])
            out.append((sh.get("name"), rows))
    return out
