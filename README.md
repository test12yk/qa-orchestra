# QA 오케스트라

QA 업무를 역할별 AI 에이전트(악기)와 지휘자로 나눠 돌리는 구성입니다. Claude Code의 에이전트 기능으로 동작하며, 회사 정보 없이 가상 샘플로 설계·검증합니다.

## 구성 (13명)

| 구분 | 신규 기능 라인 | 회귀 라인 |
|---|---|---|
| 케이스 | 신규 TC 작성가 | 회귀 TC 관리자 |
| 자동화 | 신규 기능 자동화 | 회귀 자동화 관리자 |
| 검토 | 신규 기능 검토자 | 회귀 검토자 (운영 결함 원인 분석 포함) |
| 판정 | 신규 기능 판정관 → OK면 릴리즈 버전 편입 | 릴리즈 판정관 → 배포 Go/No-Go |
| 보고 | 신규 기능 QA 리포터 | 릴리즈 QA 리포터 |

공통: 지휘자, 문서 수집가, 기획 분석가, 버그 리포터

## 원칙
1. 결함은 발견된 라인 안에서 재확인으로 닫는다. 신규 기능 결함을 곧바로 회귀 후보로 보지 않는다.
2. 회귀 편입은 회귀 TC 관리자가 별도 기준(핵심 흐름, 사업 영향, 변경 영향도 등)으로 판단한다.
3. 판정관은 요소별 근거와 추천안을 내고, 최종 결정은 사람(QA 매니저)이 한다. 기능을 빼고 배포할 때는 뺀 빌드의 재회귀 범위를 반드시 제시한다.
4. 운영 결함: 심각(Critical·Major)하거나 반복되는 결함은 원인 분석 후 회귀에 편입하고, 경미한 단발 결함은 수정·확인 후 종료한다.

## 진행 상황
- [x] 문서 수집가 `doc-collector` — 링크로 가져오기·변경 감지·붙여넣기 대체 검증(노션)
- [x] 기획 분석가 `plan-analyst` — 양식 확정, 블라인드·링크 입력 검증
- [x] 신규 TC 작성가 `new-tc-writer` — 스프레드시트(한 탭, 기본기능·리그레이션·엣지케이스), 경계값·조합 필수, 커버리지 점검
- [x] 버그 리포터 `bug-reporter` — 리포트 작성·중복 확인 → 승인 후 지라 등록(커넥터/API 토큰/초안)
- [x] 신규 기능 자동화 `new-automation` — TC·API 문서 → pytest API 테스트 → QA 서버 실행 → TC 시트 결과·이슈내용 기록(가짜 쿠폰 서버로 검증)
- [x] 신규 기능 검토자 `new-reviewer` — 실행 전 TC 검토(요구사항·충돌·위험·영향 범위 커버, 경계값·조합, 기대 결과) · 실행 결과 검토(미실행·판정 보류·가정 위의 Pass·버그) → 담당별 보완 요청 HTML
- [ ] 나머지 신규 기능 라인(판정관·QA 리포터) → 회귀 라인

## 폴더
```
.claude-plugin/marketplace.json   배포처(플러그인 목록)
plugins/
  qa-orchestra/        전체 묶음 (다른 플러그인을 의존성으로 설치)
  qa-doc-collector/    문서 수집가: agents/, tools/save_snapshot.py, defaults.json
  qa-plan-analyst/     기획 분석가: agents/, tools/render_plan_analysis.py, tools/templates/, defaults.json
  qa-bug-reporter/     버그 리포터: agents/, tools/bug_report.py(점검·지라 등록), defaults.json
  qa-new-tc-writer/    신규 TC 작성가: agents/, tools/render_test_cases.py(점검 포함), tools/revise_test_cases.py(검토 지적 보완), tools/templates/, defaults.json
  qa-new-automation/   신규 기능 자동화: agents/, tools/run_automation.py(준비·점검·실행·결과 기록), tools/templates/, defaults.json
  qa-new-reviewer/     신규 기능 검토자: agents/, tools/review.py(자동 점검·보고서), tools/templates/, defaults.json
.claude/agents/        저장소를 받아 쓸 때를 위한 연결(plugins/*/agents 를 가리킴)
qa-orchestra.config.json   팀 설정 견본
samples/coupon-discount/   가상 "쿠폰 할인" 기능 (입력 5종, 기준 답안, 실행 결과)
work/                  가져온 실제 문서와 결과 (git 제외)
```

## 설치
Claude Code 안에서 배포처를 한 번 등록한 뒤, **필요한 것만** 설치한다.
```
/plugin marketplace add test12yk/qa-orchestra
```
| 원하는 것 | 설치 명령 | 들어오는 것 |
|---|---|---|
| 전체 | `/plugin install qa-orchestra@qa-orchestra` | 모든 에이전트 (아래 플러그인들이 함께 설치됨) |
| 문서 수집가만 | `/plugin install qa-doc-collector@qa-orchestra` | `doc-collector` + 저장 스크립트 |
| 기획 분석가만 | `/plugin install qa-plan-analyst@qa-orchestra` | `plan-analyst` + 렌더러·양식 |
| 신규 TC 작성가만 | `/plugin install qa-new-tc-writer@qa-orchestra` | `new-tc-writer` + 스프레드시트 렌더러·점검·양식 |
| 버그 리포터만 | `/plugin install qa-bug-reporter@qa-orchestra` | `bug-reporter` + 점검·지라 등록 스크립트 |
| 신규 기능 자동화만 | `/plugin install qa-new-automation@qa-orchestra` | `new-automation` + 스위트 준비·실행·결과 기록 스크립트 (테스트 실행에 pytest 필요) |
| 신규 기능 검토자만 | `/plugin install qa-new-reviewer@qa-orchestra` | `new-reviewer` + 자동 점검·보고서 스크립트 |

에이전트별 플러그인에는 그 에이전트가 쓰는 파일만 들어 있다(샘플·문서는 설치되지 않음). 나중에 전체를 설치하면 빠진 것만 추가된다. 비공개 저장소라면 접근 권한이 있는 GitHub 계정이 필요하다. 설치 뒤 Claude Code를 다시 시작한다.

**저장소를 받아서** — `git clone` 후 그 폴더에서 `claude`를 실행하면 모든 에이전트가 바로 잡힌다.

준비물: Claude Code, python3(추가 설치 없음). 문서 도구 커넥터(노션 등)는 문서 수집가를 쓸 때만 필요하다.

**구글 시트로 받기**: Claude Code에서 `/mcp` → Google Drive를 연결해 두면 신규 TC 작성가가 TC를 내 드라이브에 구글 시트로 만들고 링크를 준다(비공개, 공유는 직접). 연결이 없으면 엑셀 파일(`test-cases.xlsx`)로 받는다. 끄려면 설정의 `output.google_sheets`를 `off`로.

**지라 연결**: 설정의 `jira.site`·`jira.project_key`만 회사 지라로 바꾸면 된다. 등록은 claude.ai의 Atlassian 커넥터(각자 계정) → 없으면 API 토큰(`JIRA_EMAIL`·`JIRA_API_TOKEN` 환경변수) → 둘 다 없으면 붙여넣기용 초안 순서로 시도한다. 버그 리포터는 **사용자가 등록을 지시할 때만** 지라에 올린다.

**지라 Priority·언어**: `jira.priority_from`이 `priority`면 P1~P4를 `priority_map`(Highest~Low 등)으로, `severity`면 심각도(Blocker·Critical·Major·Minor·Trivial)를 지라 Priority 칸에 넣는다. `jira.language`를 `en`으로 하면 지라 리포트를 영어로 쓴다. 등록 전 1단계에서 이슈 유형·Priority 허용값을 미리 확인해 알려준다.

**TC 실행 → 버그**: TC 시트 맨 뒤 `결과`(Pass/Fail 드롭다운)·`이슈내용` 열을 테스터가 채운다. 버그 리포터에 그 시트(xlsx·csv·구글 시트 링크)를 주면 Fail 행마다 초안을 만든다 — 사전조건·절차·기대결과·priority는 TC 그대로, 실제 결과는 이슈내용 그대로(`bug_report.py from-tc`).

## 처음 쓰는 사람
1. Claude Code에 팀이 쓰는 문서 도구(노션·컨플루언스·슬랙 등)를 커넥터로 연결한다. 문서는 **각자의 권한으로** 가져온다.
2. 팀에 맞게 바꾸려면 저장소 루트의 `qa-orchestra.config.json` 견본을 작업 폴더에 복사해 고친다(서비스 영역, 링크 도메인, 문서 종류 힌트). 없으면 각 플러그인의 기본값을 쓴다.
3. 링크만 주고 시작한다.
```
doc-collector 에이전트로 이 링크들 가져와줘. 기능 이름은 coupon-discount
- https://www.notion.so/...  (기획서)
- https://xxx.slack.com/archives/...  (슬랙)
```
가져온 문서는 `work/<기능>/inputs/`에 원본 링크·버전과 함께 저장되며 git에 올라가지 않는다. 연결 안 된 링크는 붙여넣기를 요청한다. 같은 요청을 다시 하면 바뀐 문서와 바뀐 줄을 알려 준다(`changes.md`).

## 에이전트 하나만 쓰기
필요한 에이전트의 플러그인만 설치하면 된다(위 표). 전체를 설치했더라도 **이름을 부른 에이전트만** 실행된다(입력창에서 `@`로 골라도 된다). 에이전트마다 폴더를 받아 폴더로 결과를 내므로 앞 단계를 거치지 않아도 된다.

| 에이전트 | 입력 | 출력 | 혼자 쓸 때 |
|---|---|---|---|
| `doc-collector` 문서 수집가 | 링크 목록 | `work/<기능>/inputs/` | 링크만 주면 된다 |
| `plan-analyst` 기획 분석가 | 문서 폴더 | 기획 분석서(JSON·HTML·MD) | 문서 파일을 폴더에 넣고 그 폴더를 주면 된다 |
| `new-automation` 신규 기능 자동화 | TC(json·시트) + API 문서 + QA 서버 주소 | pytest 스위트, 실행 리포트, 결과·이슈내용이 채워진 TC 시트 | TC 시트만 줘도 된다(모든 행을 후보로 봄) |
| `bug-reporter` 버그 리포터 | TC 실행 시트(Fail 행)·메모·로그 | 버그 리포트(MD) → 승인 시 지라 이슈 | 버그 메모만 줘도 된다 |
| `new-tc-writer` 신규 TC 작성가 | 기획 분석서 (없으면 문서 폴더) | TC 스프레드시트(xlsx)·CSV·HTML·MD | 문서 폴더만 줘도 되지만, 분석서가 있어야 충돌·질문이 반영된다 |
| `new-reviewer` 신규 기능 검토자 | 실행 전: 분석서 + TC json / 실행 후: + 결과 시트·자동화 결과·버그 폴더 | 검토 보고서(HTML·MD·JSON), 담당별 보완 요청 | TC json만 있어도 TC 자체 점검(기대 결과·추적·중복)은 된다 |

여러 에이전트를 이어서 돌리고 싶을 때만 지휘자(예정)를 쓴다.

**에이전트를 새로 만들 때**: `plugins/qa-<이름>/`에 그 에이전트가 쓰는 파일만 넣고, 배포처(`marketplace.json`)와 전체 묶음의 `dependencies`에 추가한다. 다른 플러그인 폴더의 파일을 참조하지 않는다.

## 기획 분석가 실행
Claude Code에서:
```
plan-analyst 에이전트로 samples/coupon-discount/inputs 를 분석해서 samples/coupon-discount/run-01 에 저장해줘
```
렌더러만 다시 돌릴 때:
```
python3 plugins/qa-plan-analyst/tools/render_plan_analysis.py samples/coupon-discount/run-01/plan-analysis.json samples/coupon-discount/run-01
```

## 신규 기능 자동화 (API)
- 설정 `automation`: `base_url`(QA 서버), `allowed_hosts`(실행해도 되는 호스트 — 여기 없는 서버에는 요청하지 않음), `auth`(헤더 이름, 토큰 환경변수 이름). 토큰은 환경변수로만 받는다.
- 결과: Pass/Fail은 결과 칸, Fail 내용은 이슈내용 칸. 판정 보류·자동화 오류는 결과를 비우고 이슈내용에 `[판정 보류]`·`[자동화 오류]` — 버그 리포터는 Fail 행만 버그 초안으로 만든다.
- 샘플로 돌려 보기: `python3 samples/coupon-discount/automation/mock_server.py` (가짜 쿠폰 QA 서버, 토큰 `sample-token`) → `QA_API_TOKEN=sample-token`으로 `new-automation` 실행. 가짜 서버는 개발문서대로 동작해서 정책과 다른 부분이 Fail로 잡힌다. 결과 예: `samples/coupon-discount/run-01/automation/` (54개 자동화: Pass 46 · Fail 3 · 판정 보류 5, 사람 확인 3 · 준비 수단 필요 1).

## 신규 기능 검토자
- 두 번 쓴다. **실행 전**(`--stage tc`): TC가 분석서의 요구사항·충돌·고위험·직접 영향 기능을 덮는지, 경계값·조합·기대 결과가 충분한지. **실행 후**(`--stage result`): 미실행·판정 보류·가정 위의 Pass·버그 없는 Fail·열린 버그를 모아 판정관이 판단할 재료가 갖춰졌는지.
- `tools/review.py collect`가 기계적으로 셀 수 있는 후보를 잡고, 에이전트가 TC·문서를 직접 읽어 확인한 것만 지적으로 남긴다(뺀 후보는 이유와 함께 보고서에 남음). 다른 에이전트의 결과물은 고치지 않고 담당별 보완 요청만 낸다. 출시 여부는 말하지 않는다(판정관 몫).
- **보완 흐름**: 검토자(실행 전) → 신규 TC 작성가 보완 모드(`revise_test_cases.py` — 지적별 추가·수정·폐기·제외 사유·답 대기, 사람이 뺀 지적은 반영 안 함(결정한 사람 필수), 타당하지 않은 지적은 근거와 함께 이견, 번호 유지, 원본은 그대로 두고 새 버전) → 검토자 재검토(`collect --previous`, 이전 지적마다 해결·일부·답 대기·남음·반영 안 함·철회 — 이견은 받아들이거나 반박, 뺀 '상' 지적은 보고서 맨 위에 '감수한 위험'으로). 예: `run-01/tc-v2/`(TC 63→66, changes.md) · `run-01/review/tc-v2/`(해결 3 · 답 대기 1 · 남음 2, 새 지적 1).
- 결과 예: `samples/coupon-discount/run-01/review/tc/`(실행 시작 가능, 지적 6건) · `review/result/`(보완 후 판정, 지적 7건 — Fail 3건이 모두 미확정 질문 Q4·Q5에 기댄 판정임을 짚음).
