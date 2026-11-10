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
- [ ] 나머지 신규 기능 라인 → 회귀 라인

## 폴더
```
.claude-plugin/marketplace.json   배포처(플러그인 목록)
plugins/
  qa-orchestra/        전체 묶음 (다른 플러그인을 의존성으로 설치)
  qa-doc-collector/    문서 수집가: agents/, tools/save_snapshot.py, defaults.json
  qa-plan-analyst/     기획 분석가: agents/, tools/render_plan_analysis.py, tools/templates/, defaults.json
  qa-bug-reporter/     버그 리포터: agents/, tools/bug_report.py(점검·지라 등록), defaults.json
  qa-new-tc-writer/    신규 TC 작성가: agents/, tools/render_test_cases.py(점검 포함), tools/templates/, defaults.json
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

에이전트별 플러그인에는 그 에이전트가 쓰는 파일만 들어 있다(샘플·문서는 설치되지 않음). 나중에 전체를 설치하면 빠진 것만 추가된다. 비공개 저장소라면 접근 권한이 있는 GitHub 계정이 필요하다. 설치 뒤 Claude Code를 다시 시작한다.

**저장소를 받아서** — `git clone` 후 그 폴더에서 `claude`를 실행하면 모든 에이전트가 바로 잡힌다.

준비물: Claude Code, python3(추가 설치 없음). 문서 도구 커넥터(노션 등)는 문서 수집가를 쓸 때만 필요하다.

**구글 시트로 받기**: Claude Code에서 `/mcp` → Google Drive를 연결해 두면 신규 TC 작성가가 TC를 내 드라이브에 구글 시트로 만들고 링크를 준다(비공개, 공유는 직접). 연결이 없으면 엑셀 파일(`test-cases.xlsx`)로 받는다. 끄려면 설정의 `output.google_sheets`를 `off`로.

**지라 연결**: 설정의 `jira.site`·`jira.project_key`만 회사 지라로 바꾸면 된다. 등록은 claude.ai의 Atlassian 커넥터(각자 계정) → 없으면 API 토큰(`JIRA_EMAIL`·`JIRA_API_TOKEN` 환경변수) → 둘 다 없으면 붙여넣기용 초안 순서로 시도한다. 버그 리포터는 **사용자가 등록을 지시할 때만** 지라에 올린다.

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
| `bug-reporter` 버그 리포터 | 실패한 TC·메모·로그 | 버그 리포트(MD) → 승인 시 지라 이슈 | 버그 메모만 줘도 된다 |
| `new-tc-writer` 신규 TC 작성가 | 기획 분석서 (없으면 문서 폴더) | TC 스프레드시트(xlsx)·CSV·HTML·MD | 문서 폴더만 줘도 되지만, 분석서가 있어야 충돌·질문이 반영된다 |

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
