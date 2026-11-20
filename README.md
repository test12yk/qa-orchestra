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
- [x] 지휘자 `/qa-conductor:conduct` — 신규 라인·회귀 라인을 따로 또는 이어서 역할 에이전트 순서대로 실행, 사람 결정 지점(수동 실행·지라 등록·편입·배포 결정·컨플루언스)에서만 멈춤, 파이프라인 파일로 중간부터 다시 시작
- [x] 문서 수집가 `doc-collector` — 링크로 가져오기·변경 감지·붙여넣기 대체 검증(노션)
- [x] 기획 분석가 `plan-analyst` — 양식 확정, 블라인드·링크 입력 검증
- [x] 신규 TC 작성가 `new-tc-writer` — 스프레드시트(한 탭, 기본기능·리그레이션·엣지케이스), 경계값·조합 필수, 커버리지 점검
- [x] 버그 리포터 `bug-reporter` — 리포트 작성·중복 확인 → 승인 후 지라 등록(커넥터/API 토큰/초안)
- [x] 신규 기능 자동화 `new-automation` — TC·API 문서 → pytest API 테스트 → QA 서버 실행 → TC 시트 결과·이슈내용 기록(가짜 쿠폰 서버로 검증)
- [x] 신규 기능 검토자 `new-reviewer` — 실행 전 TC 검토(요구사항·충돌·위험·영향 범위 커버, 경계값·조합, 기대 결과) · 실행 결과 검토(미실행·판정 보류·가정 위의 Pass·버그) → 담당별 보완 요청 HTML
- [x] 신규 기능 판정관 `new-judge` — 실행 결과 검토 → 요소 8가지(실행·결함·보류·가정·요구사항·고위험·검토 지적·영향 범위) → 편입 가능/조건부/보류 추천 + 편입 조건, 사람의 결정 기록(릴리즈 기록 파일)
- [x] 신규 기능 QA 리포터 `new-reporter` — 판정서·실행 결과 검토(+ 분석서·TC·지라 상태) → 한 장 리포트(위 1분 요약 매니저·기획용 / 아래 상세 개발·QA용), 결정 전 중간 리포트 · 결정 후 완료 리포트, HTML·MD + 승인 후 컨플루언스 페이지
- [x] 회귀 TC 관리자 `reg-tc-manager` — 회귀 스위트(영역별 탭 시트·변경 이력), 신규 TC 편입 후보(기준표·결과 확정·겹침·영역 대표) → 사람 승인분만 편입, 릴리즈 회귀 범위(필수·직접·간접·제외) → 실행 시트
- [x] 회귀 자동화 관리자 `reg-automation` — 편입된 신규 기능 테스트를 RG 번호로 옮기기, TC가 바뀐 테스트는 고칠 때까지 실행 막기, 릴리즈 범위의 자동화 케이스 실행(Fail·자동화 오류는 한 번 더 → 불안정 구분) → 회귀 실행 시트 결과·이슈내용
- [x] 회귀 검토자 `reg-reviewer` — 회귀 결과(자동화 + 수동 시트) → Fail마다 원인 4갈래(이번 변경·전부터 있던 결함·TC가 정책과 다름·환경) 추천 + 근거·담당별 할 일, 지라 운영 결함(읽기만) 원인·놓친 이유 → 심각도·반복이면 회귀 편입 후보(케이스 초안) → 회귀 TC 관리자 승인 편입
- [x] 릴리즈 판정관 `release-judge` — 기능별 편입 결정 + 회귀 검토(Fail 원인·결과 없음·열린 운영 결함) → Go / 조건부 Go / 기능 빼고 Go / No-Go 추천 + 배포 전 할 일, 기능을 빼면 다시 돌릴 회귀 범위(빠진 기능 영향 + 필수) 파일, 사람의 결정 기록
- [x] 릴리즈 QA 리포터 `release-reporter` — 릴리즈 판정서 + 회귀 검토 → 한 장 릴리즈 리포트(위 1분 요약 매니저·기획용 / 아래 상세 개발·QA용), 결정 전 중간 · 결정 후 완료, 이번 릴리즈 관련 운영 결함, HTML·MD + 승인 후 컨플루언스 페이지

## 폴더
```
.claude-plugin/marketplace.json   배포처(플러그인 목록)
plugins/
  qa-orchestra/        전체 묶음 (다른 플러그인을 의존성으로 설치)
  qa-conductor/        지휘자: skills/conduct/(SKILL.md, pipeline.py — 단계 목록·기록·다시 하기)
  qa-doc-collector/    문서 수집가: agents/, tools/save_snapshot.py, defaults.json
  qa-plan-analyst/     기획 분석가: agents/, tools/render_plan_analysis.py, tools/templates/, defaults.json
  qa-bug-reporter/     버그 리포터: agents/, tools/bug_report.py(점검·지라 등록), defaults.json
  qa-new-tc-writer/    신규 TC 작성가: agents/, tools/render_test_cases.py(점검 포함), tools/revise_test_cases.py(검토 지적 보완), tools/templates/, defaults.json
  qa-new-automation/   신규 기능 자동화: agents/, tools/run_automation.py(준비·점검·실행·결과 기록), tools/templates/, defaults.json
  qa-new-reviewer/     신규 기능 검토자: agents/, tools/review.py(자동 점검·보고서), tools/templates/, defaults.json
  qa-new-judge/        신규 기능 판정관: agents/, tools/judge.py(요소 모으기·판정서·결정 기록), tools/templates/, defaults.json
  qa-new-reporter/     신규 기능 QA 리포터: agents/, tools/report.py(재료 모으기·리포트·올린 위치 기록), tools/templates/, defaults.json
  qa-reg-tc-manager/   회귀 TC 관리자: agents/, tools/reg_suite.py(스위트·편입 후보·승인·릴리즈 범위), tools/xlsx.py, tools/templates/, defaults.json
  qa-reg-reviewer/     회귀 검토자: agents/, tools/reg_review.py(재료 모으기·검토 보고서·운영 편입 후보), tools/xlsx.py, tools/templates/, defaults.json
  qa-reg-automation/   회귀 자동화 관리자: agents/, tools/reg_auto.py(현황·옮기기·등록·실행·결과 기록), tools/xlsx.py, tools/templates/, defaults.json
  qa-release-reporter/ 릴리즈 QA 리포터: agents/, tools/release_report.py(재료 모으기·리포트·올린 위치 기록), tools/templates/, defaults.json
  qa-release-judge/    릴리즈 판정관: agents/, tools/release_judge.py(요소 모으기·판정서·재회귀 범위·결정 기록), tools/templates/, defaults.json
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
| 지휘자만 | `/plugin install qa-conductor@qa-orchestra` | `/qa-conductor:conduct` 스킬 + 파이프라인 스크립트 (부를 역할 플러그인은 따로 설치) |
| 문서 수집가만 | `/plugin install qa-doc-collector@qa-orchestra` | `doc-collector` + 저장 스크립트 |
| 기획 분석가만 | `/plugin install qa-plan-analyst@qa-orchestra` | `plan-analyst` + 렌더러·양식 |
| 신규 TC 작성가만 | `/plugin install qa-new-tc-writer@qa-orchestra` | `new-tc-writer` + 스프레드시트 렌더러·점검·양식 |
| 버그 리포터만 | `/plugin install qa-bug-reporter@qa-orchestra` | `bug-reporter` + 점검·지라 등록 스크립트 |
| 신규 기능 자동화만 | `/plugin install qa-new-automation@qa-orchestra` | `new-automation` + 스위트 준비·실행·결과 기록 스크립트 (테스트 실행에 pytest 필요) |
| 신규 기능 검토자만 | `/plugin install qa-new-reviewer@qa-orchestra` | `new-reviewer` + 자동 점검·보고서 스크립트 |
| 신규 기능 판정관만 | `/plugin install qa-new-judge@qa-orchestra` | `new-judge` + 판정서·결정 기록 스크립트 |
| 신규 기능 QA 리포터만 | `/plugin install qa-new-reporter@qa-orchestra` | `new-reporter` + 리포트 스크립트(HTML·MD·컨플루언스 본문) |
| 회귀 TC 관리자만 | `/plugin install qa-reg-tc-manager@qa-orchestra` | `reg-tc-manager` + 스위트·편입·범위 스크립트(xlsx 읽기·쓰기 포함) |
| 회귀 검토자만 | `/plugin install qa-reg-reviewer@qa-orchestra` | `reg-reviewer` + 재료 모으기·검토 보고서 스크립트 (운영 결함은 Atlassian 커넥터로 읽기) |
| 회귀 자동화 관리자만 | `/plugin install qa-reg-automation@qa-orchestra` | `reg-automation` + 옮기기·등록·실행·결과 기록 스크립트 (테스트 실행에 pytest 필요) |
| 릴리즈 QA 리포터만 | `/plugin install qa-release-reporter@qa-orchestra` | `release-reporter` + 리포트 스크립트(HTML·MD·컨플루언스 본문) |
| 릴리즈 판정관만 | `/plugin install qa-release-judge@qa-orchestra` | `release-judge` + 판정서·재회귀 범위·결정 기록 스크립트 |

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
| `new-judge` 신규 기능 판정관 | 검토자의 실행 결과 검토(review.json) (+ TC json, 지라 상태) | 판정서(HTML·MD·JSON), 사람 결정 시 릴리즈 기록 | 실행 결과 검토 review.json만 있으면 된다 |
| `new-reporter` 신규 기능 QA 리포터 | 판정서(judgment.json) + 실행 결과 검토(review.json) (+ 분석서·TC json·지라 상태) | QA 리포트(HTML·MD·컨플루언스 본문), 승인 시 컨플루언스 페이지 | 판정서와 실행 결과 검토만 있으면 된다 |
| `reg-tc-manager` 회귀 TC 관리자 | 기존 회귀 시트(xlsx·csv) / 신규 TC json + 실행 결과 검토 + 판정서 / 릴리즈 기능(판정서·릴리즈 기록) | 회귀 스위트 시트(영역별 탭), 편입 후보 페이지, 릴리즈 회귀 범위·실행 시트 | 기존 회귀 시트만 있어도 가져오기·보기·범위 고르기가 된다 |
| `reg-automation` 회귀 자동화 관리자 | 회귀 스위트 + 릴리즈 범위·실행 시트 (+ 신규 기능 자동화 폴더, API 문서, QA 서버 주소) | 회귀 pytest 폴더(registry.json), 실행 결과 페이지, 결과·이슈내용이 채워진 회귀 실행 시트 | 스위트만 있어도 현황(status)을 본다 |
| `reg-reviewer` 회귀 검토자 | 회귀 자동화 results.json + 스위트 + 릴리즈 범위 (+ 수동 결과 시트, 지라 운영 결함) | 회귀 검토 보고서(HTML·MD), 운영 결함 회귀 편입 후보(ops-candidates.json) | 회귀 결과만 있어도 된다(운영 결함 분석은 빼고) |
| `release-reporter` 릴리즈 QA 리포터 | 릴리즈 판정서(release-judgment.json) + 회귀 검토(review.json) (+ 산출물 링크) | 릴리즈 QA 리포트(HTML·MD·컨플루언스 본문), 승인 시 컨플루언스 페이지 | 판정서와 회귀 검토만 있으면 된다 |
| `release-judge` 릴리즈 판정관 | 회귀 검토(review.json) + 릴리즈 범위(selection.json) (+ 릴리즈 기록, 기능별 분석서) | 릴리즈 판정서(HTML·MD·JSON), 기능별 재회귀 범위(rerun-<기능>.json), 사람 결정 기록 | 회귀 검토와 범위만 있으면 된다(신규 기능 판정서가 없으면 '판정서 없음'으로 본다) |
| `new-reviewer` 신규 기능 검토자 | 실행 전: 분석서 + TC json / 실행 후: + 결과 시트·자동화 결과·버그 폴더 | 검토 보고서(HTML·MD·JSON), 담당별 보완 요청 | TC json만 있어도 TC 자체 점검(기대 결과·추적·중복)은 된다 |

여러 에이전트를 이어서 돌리고 싶을 때만 지휘자(`/qa-conductor:conduct`)를 쓴다.

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

## 신규 기능 판정관
- 입력은 검토자의 **실행 결과 검토**. 요소 8가지를 설정 기준(`judge`: 릴리즈 불가 심각도 Blocker·Critical, 조건부 릴리즈 Major, 최상위 우선순위 P1)으로 매기고, 기준표 후보와 다르게 추천하면 이유를 남긴다.
- 조건부·보류면 편입 조건(K-01…, 담당·확인 방법)을 낸다. 결정은 사람이 하고 `judge.py decide`로 기록한다(추천과 다른 결정은 이유 필수, `--ledger`로 릴리즈 기록 파일에 남김).
- QA 매니저가 Claude를 쓰지 않아도 된다: 판정서를 claude.ai 링크로 공유하면 맨 아래 '결정 남기기' 칸에서 결정·조건·이유를 저장하고(참여자 이상 권한), 판정관이 그 제출을 `decide --source`로 판정서에 옮긴다.
- 결과 예: `samples/coupon-discount/run-01/judgment/` — 편입 보류 추천(Critical QAO-8, P1 미실행·보류 4개), 단 Fail 3건이 모두 Q4·Q5 가정 기준이라 답에 따라 조건부로 바뀔 수 있음. 결정은 비워 둠.

## 신규 기능 QA 리포터
- 앞 단계 결과를 바꾸지 않고 모아 **한 장**으로 전한다. 위 '요약'(한 줄 요약·요점, 결정/추천, 숫자, 남은 일·담당, 답이 필요한 질문, 범위)은 QA 매니저·기획용, 아래 '상세'(결함, 우선순위·유형별 실행 결과, 통과 못 한 TC, 요구사항, 영향 받는 기존 기능, 검토 지적, 근거 자료)는 개발·QA용.
- 판정서에 사람의 결정이 없으면 **중간 리포트(결정 대기)**, 결정이 기록되면 **QA 완료 리포트**(받아들인 조건 표시). 리포트 이후 판정서에 새 결정이 생기면 그릴 때 경고한다.
- 요약 요점에는 근거 번호(TC·버그·질문·조건)가 있어야 그려진다.
- **컨플루언스**: `report.confluence.html`(Atlassian 형식 본문)을 만들어 두고, 사용자가 원하면 위치·제목을 보여 주고 승인받은 뒤 Atlassian 커넥터로 올린다. 설정 `confluence`(site·space_key·parent_page_id)만 바꾸면 회사 컨플루언스로 간다. 올린 페이지는 `report.py record`로 남겨 다음엔 같은 페이지를 고친다.
- 결과 예: `samples/coupon-discount/run-01/report/` — 중간 리포트(결정 대기, 추천 편입 보류).

## 회귀 TC 관리자
- **스위트**: `suite.json`이 원본, `regression-suite.xlsx`는 Domain별 탭 + '변경 이력' 탭(신규 TC 시트 열 + 자동화·출처·편입일). 기존 회귀 시트는 `import`로 가져온다(머리글은 설정 `suite.columns`).
- **편입 후보**: 신규 TC마다 핵심 흐름(+2)·사업 영향(+1)·변경 영향(+1)·자동화 가능(+1)으로 점수 → 3점 이상 넣기, 1점 이하 빼기. 결과가 Pass가 아니거나 질문 답 전 임시 가정으로 낸 Pass는 나중에. 영역마다 대표 1개·기능당 최대 5개(넘기려면 이유), 기존 케이스와 겹치면 관리자가 읽고 합치거나 뺀다. 이 기능으로 기대 결과가 바뀌는 기존 케이스는 '고치기' 후보.
- **승인**: QA 매니저가 고른 번호만 `apply`로 넣는다(새 RG 번호, 변경 이력). 기능 편입 결정 전이면 거부, 이미 넣은 번호·가정 기준 결과도 거부.
- **릴리즈 범위**: P1·지난 회귀 Fail = 필수, 이번 기능에서 편입된 케이스·직접 영향 영역 = 전부, 간접 영향 = P1·P2, 나머지 = 이번엔 제외(이유). 관리자가 놓친 연결만 조정(`overrides`). `regression-run.xlsx`에 이번에 돌릴 케이스만.
- 결과 예: `samples/coupon-discount/regression/` — 기존 스위트 26개(가상 쇼핑몰), 쿠폰 TC 63개 → 넣기 5(기능당 최대 5·영역당 1) · 나중에 41 · 빼기 17, 2026.12 릴리즈 범위 17/26(필수 8 · 직접 5 · 간접 4 · 제외 9). 승인·편입은 하지 않음.

## 회귀 자동화 관리자
- **현황**: 스위트에서 자동화(API)인 케이스마다 실행 가능 · 테스트 고치기 필요 · 자동화 아직 안 됨(옮기기/작성) · 수동으로 바꿀 후보(API로 끝까지 확인할 수 없음, 이유와 "자동화하려면" 필요한 API). `registry.json`에 케이스별 테스트, 기준 TC 지문, 코드 지문, 출처가 남는다.
- **옮기기**: 회귀 TC 관리자가 편입한 케이스(출처 `<기능> TC-xxx`)의 신규 기능 테스트를 `test_RG_xxxx_…`로 이름만 바꿔 회귀 폴더로 복사한다(도우미·준비 코드 포함, 신규 기능 폴더는 그대로). 신규 기능 자동화에서 Pass가 아닌 TC는 옮기지 않는다.
- **수동으로 바꾸기**: API로 끝까지 확인할 수 없는 케이스는 수동 케이스다. 회귀 자동화 관리자는 `to-manual.json`으로 넘기기만 하고, 회귀 TC 관리자가 `set-automation --list`로 스위트 표시를 수동으로 바꾼다(변경 이력). 바뀌기 전 실행에서는 `[수동으로 확인]`으로 테스터에게 넘긴다.
- **TC가 바뀌면**: 테스트를 만든 뒤 사전조건·절차·기대 결과가 바뀐 케이스는 실행하지 않고 '테스트 고치기 필요'로 둔다. 고친 내용(`--note`)과 함께 다시 등록해야 풀린다(코드가 그대로면 거부).
- **실행**: 릴리즈 범위에 든 실행 가능 케이스만 돌린다. Fail·자동화 오류는 한 번 더 돌리고, 그때 통과하면 Pass가 아니라 **불안정**. 결과는 회귀 실행 시트의 결과·이슈내용에 적고, 구글 시트로 올리면 `link`로 결과 페이지에 링크를 건다(불안정·고치기 필요·자동화 아직 안 됨·수동으로 확인은 결과 칸을 비우고 사람이 확인).
- 결과 예: `samples/coupon-discount/regression/automation/` — 기존 API 표시 19개 중 테스트 8개(직접 작성), 11개는 API로 확인할 수 없어 수동으로 바꿈(샘플 API에 없는 로그인·정산 등, 화면 안내가 기대 결과인 RG-0015 → 스위트 v2). 2026.12 범위 17개 = 자동화 8 · 수동 9, 가짜 서버 실행 Pass 5 · Fail 3(RG-0011 보유 초과 포인트 입력 거부, RG-0018 취소 시 포인트 미복구, RG-0020 배송비 미차감 환불). 편입 승인 전이라 쿠폰 TC 옮기기는 하지 않음.

## 회귀 검토자
- **Fail 원인**: Fail마다 4갈래 중 하나를 근거와 함께 추천한다 — 이번 변경으로 깨짐 · 전부터 있던 결함 · TC가 정책·명세와 다름 · 환경·데이터 문제. 도구가 지난 회귀 결과·변경 영향으로 후보를 주고, 검토자가 문서를 읽어 정한다. 근거가 모자라면 '확인 필요' + 질문(누구에게 무엇을). 확정은 담당자.
- **담당별 할 일**: 결함 → 버그 리포터 등록 → 개발, TC 문제 → 회귀 TC 관리자(기존 케이스 고치기), 환경 → 회귀 자동화 관리자, 결과 없는 수동 케이스 → 테스터. 결과 없는 케이스가 있으면 '남은 확인 뒤 판정'.
- **운영 결함**: 지라에서 설정 `ops.jql`로 읽기만 한다. 티켓마다 원인·놓친 이유(TC 없음·범위 제외·TC가 조건 안 봄·자동화 빈틈·QA 환경 재현 불가)를 쓰고, 심각도 Blocker·Critical·Major이거나 같은 영역 90일 안 2번 이상이면 회귀 편입 후보 + 케이스 초안(기대 결과는 티켓에서만). 회귀 TC 관리자가 `apply-ops`로 승인분만 넣는다(수정 완료·확인 필요 없음만).
- 결과 예: `samples/coupon-discount/regression/review-2026.12/` — Fail 3개: RG-0018 이번 변경으로 깨짐(근거 일부), RG-0011 TC가 명세와 다름(기획 확인 필요), RG-0020 환경(QA 서버 배송비 미계산, 개발 확인 필요). 수동 9개 결과 없음 → 남은 확인 뒤 판정. 가상 운영 결함 5건 → 회귀 편입 후보 3(SHOP-201 Critical · SHOP-212 Major(수정 중) · SHOP-210+198 반복), 종료 1. 승인·편입은 하지 않음.

## 릴리즈 판정관
- **추천 4가지**: Go · 조건부 Go(배포 전 할 일이 남음) · 기능 빼고 Go · No-Go. 도구가 기준표로 후보를 주고, 판정관이 상황(핵심 흐름·고객 노출·회피 수단·다른 변경과의 의존)을 보고 정한다. 후보와 다르면 이유를 쓴다. 결정은 QA 매니저.
- **릴리즈 불가 기준**: 기능 편입 보류·결정 전, 필수(P1·지난 Fail) 회귀 결과 없음, 결함으로 본 P1·P2 Fail, 원인 확인 필요인 P1 Fail. 나머지 결과 없음·원인 확인 필요·열린 Blocker·Critical·Major 운영 결함(이번 빌드에 수정이 들어가나)은 조건부. 이번 변경 원인인 이슈에는 관련 기능이 붙어, 그 기능을 빼면 풀리는지 보인다.
- **기능 빼면 재회귀**: 빠진 기능의 영향 케이스 + 필수 케이스를 뺀 빌드로 다시 돌린다(빠진 기능에서 편입된 케이스는 빠지고, 나머지는 이번 결과 그대로). `rerun-<기능>.json`은 selection 형식이라 회귀 자동화 관리자 `run --selection`에 그대로 쓴다.
- **결정**: 판정관에게 말로 전하거나, claude.ai 판정서 링크의 '결정 남기기' 칸(결정·뺄 기능·받아들이는 할 일·이유)에 남기면 판정관이 옮긴다.
- 결과 예: `samples/coupon-discount/regression/release-judgment-2026.12/` — No-Go 추천. 필수 수동 RG-0001·0015 결과 없음(기능과 무관해 쿠폰을 빼도 남음), RG-0018 P1 Fail(쿠폰 영향 영역이지만 SHOP-201 수정 영향일 수도), 쿠폰 편입 결정 전. 선택지: 필수 Pass + RG-0018이 쿠폰 때문이면 쿠폰 빼고 Go(재회귀 16개 = 자동화 8 · 수동 8). 결정은 비워 둠.

## 릴리즈 QA 리포터
- **한 장, 위아래 구분**: 위는 1분 요약(매니저·기획용). 한 줄 요약, 릴리즈 불가 이슈, 내용별 요점, 결정 상태, 숫자, 배포 전에 할 일, 선택지. 아래는 상세(개발·QA용). 기능 판정, 범위별 회귀 결과, Fail 원인·다음 할 일, 결과 없는 케이스, 뺀 빌드 재회귀, 운영 결함, QA 산출물.
- **결정 전에도**: 판정서에 결정이 없으면 '중간 리포트 · 결정 대기', 결정이 기록되면 '릴리즈 완료 리포트'.
- **운영 결함은 이번 릴리즈 관련만**: 아직 열린 것(이번 빌드에 수정이 들어가나)과 관련 회귀 케이스가 이번 범위에 든 것.
- **공유**: HTML·MD 기본, 승인 후 컨플루언스 페이지(설정 `confluence` 블록만 바꾸면 회사 컨플루언스). 다시 올릴 때는 같은 페이지를 고친다.
- 결과 예: `samples/coupon-discount/regression/release-report-2026.12/` — 중간 리포트(결정 대기, 판정관 No-Go 권고). 릴리즈 불가 3개, 할 일 8개, 이번 릴리즈 관련 운영 결함 4/5건.

## 지휘자
- **부르기**: `/qa-conductor:conduct 장바구니 쿠폰 할인 QA 해줘`, `/qa-conductor:conduct 2026.12 회귀 돌려줘`, `/qa-conductor:conduct 이어서 해줘`. 저장소를 받아 쓸 때는 `/conduct`.
- **범위**: 신규 라인(문서 수집 → 기획 분석 → TC → 실행 전 검토 → 자동화 → 수동 실행 → 버그 → 실행 결과 검토 → 판정 → 편입 결정 → 리포트), 회귀 라인(릴리즈 범위 → 회귀 자동화 → 수동 실행 → 회귀 검토 → 릴리즈 판정 → 배포 결정 → 릴리즈 리포트), 또는 둘을 이어서(편입 결정 → 회귀 편입 후보 → 승인 → 릴리즈 범위 …).
- **멈추는 곳**: 사람이 할 곳만 — 수동 실행(테스터), 기능 편입 결정·회귀 편입 승인·배포 결정(QA 매니저), 그리고 역할이 승인을 요청한 일(지라 등록, 구글 시트·컨플루언스 올리기). 사람이 정했다고 말하기 전에는 기록하지 않는다.
- **진행 기록**: `work/pipelines/<이름>.json`·`.md`. 단계가 끝나면 출력 파일이 있어야 기록되고, 검토 보완·기능 빼고 재회귀는 `reopen`으로 그 단계부터 다시 한다. 대화가 끊겨도 '이어서 해줘'로 이어간다.
- 지휘자는 각 역할을 부르기만 한다 — 역할 플러그인이 설치돼 있어야 하고(전체 묶음이면 모두), 없으면 설치 명령을 알려 준다.
