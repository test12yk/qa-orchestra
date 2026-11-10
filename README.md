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
- [ ] 신규 TC 작성가
- [ ] 버그 리포터
- [ ] 나머지 신규 기능 라인 → 회귀 라인

## 폴더
```
.claude-plugin/            플러그인·마켓플레이스 정보
agents/                    에이전트 정의 (.claude/agents 는 여기로 연결)
qa-orchestra.config.json   팀별 설정(서비스 영역, 링크→커넥터, 문서 종류)
tools/save_snapshot.py     가져온 문서 저장·버전·변경 diff
tools/render_plan_analysis.py   기획 분석서 JSON → HTML·마크다운
work/                      가져온 실제 문서와 결과 (git 제외)
tools/templates/           양식(CSS, JSON 예시)
samples/coupon-discount/   가상 "쿠폰 할인" 기능
  inputs/                  입력 문서 5종 (의도적 충돌 포함)
  outputs/                 사람이 만든 기준 답안
  run-01/                  에이전트 실행 결과
```

## 설치
**플러그인으로 (아무 폴더에서나 쓰기)** — Claude Code 안에서:
```
/plugin marketplace add test12yk/qa-orchestra
/plugin install qa-orchestra@qa-orchestra
```
비공개 저장소라면 그 저장소에 접근 권한이 있는 GitHub 계정으로 로그인돼 있어야 한다. 설치 뒤 Claude Code를 다시 시작한다.

**저장소를 받아서** — `git clone` 후 그 폴더에서 `claude`를 실행하면 에이전트가 바로 잡힌다.

준비물: Claude Code, python3(추가 설치 없음). 문서 도구 커넥터(노션 등)는 문서 수집가를 쓸 때만 필요하다.

## 처음 쓰는 사람
1. Claude Code에 팀이 쓰는 문서 도구(노션·컨플루언스·슬랙 등)를 커넥터로 연결한다. 문서는 **각자의 권한으로** 가져온다.
2. `qa-orchestra.config.json`에서 서비스 영역, 링크 도메인, 문서 종류 힌트를 팀에 맞게 고친다.
3. 링크만 주고 시작한다.
```
doc-collector 에이전트로 이 링크들 가져와줘. 기능 이름은 coupon-discount
- https://www.notion.so/...  (기획서)
- https://xxx.slack.com/archives/...  (슬랙)
```
가져온 문서는 `work/<기능>/inputs/`에 원본 링크·버전과 함께 저장되며 git에 올라가지 않는다. 연결 안 된 링크는 붙여넣기를 요청한다. 같은 요청을 다시 하면 바뀐 문서와 바뀐 줄을 알려 준다(`changes.md`).

## 기획 분석가 실행
Claude Code에서:
```
plan-analyst 에이전트로 samples/coupon-discount/inputs 를 분석해서 samples/coupon-discount/run-01 에 저장해줘
```
렌더러만 다시 돌릴 때:
```
python3 tools/render_plan_analysis.py samples/coupon-discount/run-01/plan-analysis.json samples/coupon-discount/run-01
```
