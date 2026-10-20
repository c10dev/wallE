# walle

[English](README.md) · **한국어**

walle는 코딩 에이전트가 읽는 지시 파일을 수거하고, 압축하고, 분류한다.
CLAUDE.md, AGENTS.md, 스킬, MEMORY.md, Cursor·Copilot·Gemini·Windsurf·Cline 규칙을 읽는다.
그리고 그 파일이 실제 세션에서 드는 비용을 잰다.

압축기처럼 일한다. 쌓인 것을 모으고, 중복을 눌러 합치고, 나머지를 분류하고, 남길 것은 남긴다.

## 쓰는 법

```bash
python3 bin/walle scan ~/projects --html   # 정적 점검
python3 bin/walle eve --since 2026-10-01   # 세션 실측
python3 bin/walle diff 이전/scan.json 이후/scan.json
python3 bin/walle check . --max-tokens 15000  # CI용, 넘으면 종료 코드 1
python3 bin/walle judge --dry-run          # 판정할 항목(파일:줄)과 추정 토큰
python3 bin/walle judge                    # 후보를 배치 파일로 내보냄 → 에이전트가 답 파일을 씀
python3 bin/walle judge --import           # 답을 읽어 judge.md 작성
```

리포트는 `~/.walle/runs/<시각>/`에 쓴다. 스캔마다 `~/.walle/runs/index.jsonl`에 한 줄을 더한다. 점검하는 폴더에는 쓰지 않는다. `walle scan`과 `walle check`는 스캔하는 폴더 안의 `--out` 폴더(그 옆 `cache/` 포함)를 거부하고 종료 코드 2로 끝난다.

## scan.json (schema 2)

모든 표면은 `scan.json`을 읽는다. schema 2는 필드를 더하기만 하고 이름을 바꾸지 않는다. schema 1을 읽던 코드는 그대로 돈다.

- `run`: 실행 id(실행 폴더 이름), `parent_id`(같은 루트의 직전 스캔), `label`(`--label`), `walle_version`, `rules_hash`, `estimator`, `counting_revision`, `roots`, `git_heads`
- `items`: 읽거나 고칠 것 하나에 항목 하나. 한 파일의 날짜·모델명 줄(3줄부터), 한 MEMORY.md의 줄, 두 파일 사이에 이어진 복사 줄, 같은 스택의 다른 파일을 통째로 복사한 파일(`file_copy` 노트)은 각각 한 항목이다. 항목 id는 규칙, 원문, 파일 경로의 해시 앞 6자리다. 줄이 옮겨져도, 폴더를 따로 스캔해도 같고, 두 폴더의 같은 줄은 서로 다르다. 경로는 이 기계의 경로라서 다른 곳의 클론은 id가 다르다. 위치에는 잘리지 않은 원문, `end_line`, 해시가 있다. 이어진 복사 줄은 줄마다 위치 하나라서, DELETE가 그 사이의 다른 줄을 가리키지 않는다.
- `triage`: 스택마다 판정, 토큰 구성, 여기부터(`start_here`) 항목 id, 접힌 묶음
- `files[]`: `sha`, `ownership`, `##` 절의 줄 범위도 담는다

토큰 수는 bytes-v2 추정기로 센다. `walle context`와 같은 산문 비율이라서 한 파일은 두 명령에서 같은 수다. Claude Code가 읽는 파일은 HTML 주석 블록을 빼고 센다. Claude Code가 그 주석을 버리기 때문이다. Codex 파일은 통째로 센다. `--estimator v1`은 bytes-v1(ASCII 4자당 1토큰)로 세고 기본 예산은 8,000이다. HTML 주석 블록을 빼고 세면, 작성자의 기계에서 15,000 bytes-v2 토큰과 8,000 bytes-v1 토큰은 같은 스택을 예산 초과로 판정한다(2026-10-20). 다른 토큰 기준은 bytes-v1 값에 첫 bytes-v2 비율(14,500 / 8,000)을 곱해 두 자리로 반올림한 값이다. 예산이 바뀔 때 그대로 두었다.
claude.ai에서 동기화한 스킬과 플러그인 스킬은 예산 스택에 넣지 않는다. 그 비용은 `walle context`가 보여 준다.
판정(예산 기본 15,000 추정 토큰, `--max-tokens`): 예산을 넘으면 `OVER BUDGET`. 예산 이하이고, 스택(공용 파일 포함)에 확실한 DELETE 줄이 180 토큰(`--estimator v1`은 100 토큰) 이상이거나 충돌(함께 읽히는 달라진 두 줄)이 있거나, 내 파일에 열린 후보가 3개 이상이면 `TRIM`. 나머지는 `LEAN`.
한 스택의 `topic`·`protocol` 쌍은 용어나 메시지만 같고 글은 다르다. 실제 파일에서는 대개 같은 말이다. 그래서 `term_clash` 확인 후보로 나머지 묶음에 두고, 여기부터와 판정에는 넣지 않는다. `walle judge`가 읽는다.
여기부터는 내 파일(폴더의 git 체크아웃과 그 폴더의 MEMORY.md)에서 최대 3개다. 큰 층(MOVE)은 3,600 토큰(`--estimator v1`은 2,000 토큰)을 넘고 스택의 40%를 넘는 내 파일이다. 세션 시작에 읽히는 항목만 판정과 여기부터에 든다.
DELETE는 넓은 쪽 사본을 남긴다. 지울 줄을 읽는 모든 스택이 남길 줄도 읽을 때만 DELETE이고, `~/.claude/CLAUDE.md` 같은 공용 파일의 줄은 지우라고 하지 않는다.
두 실행의 발견 수는 `rules_hash`가 같을 때만, 토큰 합은 `estimator`와 `counting_revision`이 같을 때만 비교한다. `counting_revision` 2는 Claude Code가 읽는 파일을 HTML 주석 블록 없이 센 실행이다. 이 값이 없는 실행은 1(파일 전체)로 본다.

## 보는 것

- **세션 시작 스택:** 폴더마다 첫 프롬프트 전에 읽히는 지시와 그 크기
- **원인 1 무관 비용(후보):** 작업과 관계없이 단계를 더하는 지시("항상", "먼저 읽어라", "every task")
- **원인 2 충돌(후보):** 세 가지 쌍을 찾는다. 뜻은 `walle judge`로 에이전트가 판정한다.
  - drift: 비슷하지만 달라진 규칙
  - topic: 한 세션에 함께 읽히고 핵심어(백틱 토큰, 대문자 용어, `SendMessage` 같은 도구 이름)를 공유하는데, 한쪽만 부정하거나("보낸다"와 "보내지 않는다") 같은 메시지를 다른 형식으로 쓴 규칙. 한 파일 안의 두 규칙도 본다.
  - protocol: 두 저장소가 같은 메시지(`READBACK D-xxxx`)를 다른 형식으로 쓴 규칙
- **원인 3 모호한 품질:** "깔끔하게", "AI스럽지 않게", "신중히", "best practices"
- **원인 4 구모델 보정:** 모델 이름, 날짜, NEVER/절대 겹침, 같은 규칙 반복
- **워크트리 사본:** 같은 지시 파일이 워크트리마다 다르게 갈라진 곳

"후보"는 줄을 읽고 사람이나 `walle judge`가 판정한다.
`walle judge`는 모델을 부르지 않고 API 키도 쓰지 않는다. 후보를 배치 파일로 내보내면, 사용자의 에이전트(또는 배치마다 서브에이전트)가 루브릭대로 판정해 답 파일을 쓴다. `--import`가 답을 검증해 실제 문제인지, 어느 줄을 남길지, 한 줄 수정안을 `scan.json` 옆 `judge.md`에 쓴다.
판정은 `~/.walle/cache/judge.json`에 규칙 글과 루브릭 버전의 해시로 캐시한다. 다음에는 글이 바뀐 규칙만 내보낸다.
기본은 배치 파일만 보고 판정한다(quick). `--deep`이면 판정하는 에이전트가 원문 파일까지 열어 본다. 더 많이 찾지만 비용이 훨씬 크다(실측 약 14배).
walle는 기기 밖으로 아무것도 보내지 않는다.

## 설치 경로

- Claude Code 플러그인: `claude plugin marketplace add <경로>` → `claude plugin install walle@walle`
  - 세션 시작 알람은 기본으로 꺼져 있다. `session_alarm`을 켜고 `budget_tokens`(기본 15,000)를 정하면, 폴더의 스택이 예산을 넘을 때 한 줄을 알려 준다.
  - 알람은 bytes-v2 토큰으로 센다. 예전 bytes-v1 기준으로 `budget_tokens`를 8000으로 저장했다면 15000으로 바꾼다.
- CLI: `pipx install walle-cli` (패키지 이름은 `walle-cli`, 명령은 `walle`)
- Codex와 다른 에이전트: CLI를 pipx로 설치하고 `skills/walle`을 스킬 폴더에 복사
- CI: `walle check . --max-tokens 15000`

## 한계

[README.md](README.md#limits)의 Limits와 같다.
