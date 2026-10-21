# walle

[English](README.md) · **한국어**

walle는 코딩 에이전트가 읽는 지시 파일을 수거하고, 압축하고, 분류한다.
CLAUDE.md, AGENTS.md, 스킬, MEMORY.md, Cursor·Copilot·Gemini·Windsurf·Cline 규칙을 읽는다.
그리고 그 파일이 실제 세션에서 드는 비용을 잰다.

압축기처럼 일한다. 쌓인 것을 모으고, 중복을 눌러 합치고, 나머지를 분류하고, 남길 것은 남긴다.

## 쓰는 법

```bash
python3 bin/walle scan ~/projects --html   # 정적 점검
python3 bin/walle scan ~/projects --progress json  # 단계(collect, stack, classify, pairs, triage)의 시작과 끝마다 stderr에 JSON 한 줄. 끝 이벤트에 files, pairs, items, 마지막에 done. keep released 줄은 note 이벤트
python3 bin/walle eve --since 2026-10-01   # 세션 실측: 이 폴더, 모델 계열마다 한 행, 중앙값 [p25-p75]과 n
python3 bin/walle eve --around <apply-id>  # apply 전후 비교(양쪽 10세션 이상일 때만 변화 판정)
python3 bin/walle context --compare A B    # 두 세션의 첫 호출을 원천별로 비교(apply id 하나: 전후 최신 세션)
                                           # apply id 하나: 적용한 행마다 실은 세션과 쓴 세션 수, 화면(entrypoint)이 둘 이상이면 화면별,
                                           # 전후 최신 세션이 그 원천을 실었는지(아직 실림, 빠짐)와 각 세션의 화면
python3 bin/walle context --agent codex    # Codex 세션 첫 호출을 원천별로 실측(--usage: 실린 것 대 쓴 것)
python3 bin/walle context --usage --json    # context.json을 stdout에 출력(실행 기록은 그대로 씀, --compare와는 못 씀)
python3 bin/walle diff                     # 기준 실행(고정, 없으면 직전 스캔) -> 이 폴더의 최신 스캔. last: 최근 두 스캔
python3 bin/walle diff 이전/scan.json 이후/scan.json
python3 bin/walle runs                     # 실행 목록(스캔, 계획, apply, undo, diff, context, measure, compare). walle pin <run>으로 기준 실행 고정
                                           # 실행마다 runs 폴더의 index.html(이력 페이지)을 다시 쓴다
python3 bin/walle check . --max-tokens 15000  # CI용, 넘으면 종료 코드 1
python3 bin/walle judge --dry-run          # 판정할 항목(파일:줄)과 추정 토큰
python3 bin/walle judge                    # 후보를 배치 파일로 내보냄 → 에이전트가 답 파일을 씀
python3 bin/walle judge --import           # 답을 읽어 judge.md 작성
python3 bin/walle trim --plan              # 안 쓰는 커넥터·플러그인·스킬·훅을 덜어낼 계획(바꾸지 않음)
python3 bin/walle trim --import            # 계획의 판단 배치 답을 읽어 계획에 넣음
python3 bin/walle apply <run> --only <id>  # 고른 설정 키만 백업 뒤 바꿈. 수동 행은 체크리스트. --dry-run은 쓰지 않음
python3 bin/walle undo <apply-id>          # 파일이 apply 직후 그대로일 때만 백업을 복원
python3 bin/walle keep <id> --why "이유"   # 줄이나 원천을 남김. 판정, 여기부터, 계획에서 빠짐
python3 bin/walle keep --list              # 남긴 목록. 해제는 walle unkeep <id>
```

`walle trim --plan`은 기간의 세션(`walle context --usage`와 같은 자료)과 runs 폴더의 최신 스캔을 읽는다. 동작마다 한 행이다: 바꿀 것(설정 키, patch, 앱 안의 자리), 누가 하는지(walle, 수동, patch), 세션당 절감 하한(bytes-v2 토큰), 실린 세션과 쓴 세션, 위험, 되돌리기, 나중 세션의 확인 방법. 지시 파일 변경은 실행 폴더의 `patches/`에 patch 파일로만 쓴다. "이 사용자에게 필요한가"를 판단할 행은 judge 배치 파일로 낸다. 아무것도 바꾸지 않는다. `walle apply`는 계획 단계 C5다.

- claude.ai가 Claude Code로 동기화한 플러그인(`~/.claude/plugins/synced/`의 폴더)은 walle 행이다: `enabledPlugins["<이름>@synced"] = false`. `claude plugin disable <이름>@synced`가 쓰는 키다([문서](https://code.claude.com/docs/en/plugins/loading#control-which-synced-plugins-load)). 조직이 필수로 둔 플러그인은 이 키를 무시한다. 다음 동기화 뒤에도 남는지는 문서에 없다. 이 키는 `cli`, `sdk-cli` 세션에서 듣는다. `claude-desktop`에는 닿지 않는다(다음 항목).
- 데스크톱 호스트는 claude.ai 플러그인을 `~/.claude/remote/plugins`의 사본에서 `--plugin-dir`로 `claude-desktop` 세션에 넘긴다. 이 사본의 id는 `<manifest 이름>@inline`이고, `enabledPlugins["<이름>@inline"] = false`만 이것을 끈다([문서](https://code.claude.com/docs/en/plugins/loading#keep-a-session-only-plugin-from-loading)). 2026-10-21에 Claude Code 2.1.299의 `claude plugin list`와 실제 데스크톱 사본으로 측정했다: `@inline` 키는 플러그인을 껐고, `@synced` 키는 끄지 못했다. walle는 사본이 가장 새 `.synced` 묶음(호스트가 지금 넘기는 묶음)에 있는 플러그인마다 `@inline` 행을 낸다. 그 절감은 `claude-desktop` 세션만 센다. 동기화도 된 플러그인은 두 행을 받는다. 이 키는 같은 이름의 플러그인을 `--plugin-dir`로 싣는 다른 세션에서도 그 플러그인을 끈다. 열려 있는 세션은 플러그인을 그대로 갖고, 다음 세션부터 빠진다. 안전 플러그인, 조직이 필수로 둔 플러그인(키가 그 정책을 우회한다), 호스트가 지금 넘기지 않는 사본에는 `@inline` 행을 내지 않는다. 되돌릴 때는 `walle undo`를 쓴다. `claude plugin enable <이름>@inline`은 쓰지 않는다: 이 명령은 `"<이름>@synced": false`를 쓴다. `anthropic-skills@inline`은 내지 않는다: claude.ai 스킬 전부가 `claude-desktop`에서 한꺼번에 빠진다.
- 실제 플러그인의 스킬(claude.ai 스킬 동기화가 아닌 플러그인의 `<플러그인>:*`)에는 `skillOverrides`를 내지 않는다. Claude Code가 플러그인 스킬에는 적용하지 않는다([문서](https://code.claude.com/docs/en/skills#override-skill-visibility-from-settings)). 대신 플러그인 행이 플러그인 전체를 뺀다.
- 동기화된 claude.ai 스킬(`anthropic-skills:*`)은 `skillOverrides` 행이다. `cli`와 `sdk-cli`는 이 스킬을 사용자 스킬로 읽고, 사용자 스킬에는 `skillOverrides`가 적용된다. 이 내용은 Claude Code 2.1.303 코드를 읽은 결과이고, 아직 `cli`·`sdk-cli` 세션으로 확인하지 않았다. `claude-desktop`은 이 스킬을 플러그인으로 읽는다. 2026-10-21에 `claude-desktop` 2.1.299에서 측정했다: 키를 넣은 뒤에도 스킬이 목록에 남았다.
- 표면(surface)은 세션의 `entrypoint`다(`cli`, `sdk-cli`, `claude-desktop`). 동기화된 스킬과 `@synced` 플러그인 키의 절감은 `cli`와 `sdk-cli` 세션만 센다. `@inline` 플러그인 키의 절감은 `claude-desktop` 세션만 센다. 나머지 몫은 따로, 확인되지 않은 수로 보인다. plan.md의 합계도 같은 방식으로 나눈다.
- 이미 넣은 키는 넣은 뒤 시작한 세션으로 표면마다 상태를 낸다: `already set`, `set, still listed on <표면>`, `set, the source is gone on <표면>`(지금 버킷에 없는 동기화 스킬), `set, no baseline on <표면>`(키를 넣기 전에 그 표면의 어느 세션도 원천을 싣지 않았다), `set, not measured on <표면>`. 넣은 시각은 마지막 `walle undo` 뒤에 그 키를 넣은 가장 이른 `walle apply`다. 없으면 설정 파일을 마지막으로 바꾼 시각이고, 키는 그 시각이나 그 전에 들어갔다. 그 절감은 세지 않는다. 키를 지우는 행은 없다.
- 현재 동기화 버킷에 없는 스킬은 "not in the current bucket"으로 표시한다. 그 실린 세션 수는 지난 세션이다.
- 조직이 필수로 둔 플러그인과 호스트가 지금 넘기지 않는 데스크톱 사본은 수동 행이다. claude.ai 커넥터도 수동 행이다(Customize > Connectors, claude.ai/customize/connectors). claude.ai에서 구성원은 "Installed by default" 플러그인을 끌 수 있다. "Required" 플러그인은 조직 Owner가 바꿔야 한다([문서](https://claude.com/docs/plugins/admin)).
- `walle apply`는 `enabledPlugins` 키(`<이름>@synced`, `<이름>@inline` 포함)를 `false`로만 바꾸고, `skillOverrides` 키는 `user-invocable-only`로만 넣는다. 설정 키를 지우지 않는다. 다른 동작은 모두 거부한다.

`walle keep`은 runs 폴더 옆 `keep.json`(기본 `~/.walle/keep.json`)에 쓴다. 남긴 줄이 바뀌면 다음 스캔이 `keep released: line changed`를 한 번 보이고 다시 센다. 팀은 저장소 루트의 `.wallekeep`에 팀 키를 한 줄씩 둔다. 팀 키는 원격 URL과 저장소 안 경로로 만들어서 모든 클론에서 같다. walle는 `.wallekeep`을 읽기만 한다. `walle keep --print <id>`가 붙일 줄을 낸다.

리포트는 `~/.walle/runs/<시각>/`에 쓴다. 스캔마다 `~/.walle/runs/index.jsonl`에 한 줄을 더한다. 점검하는 폴더에는 쓰지 않는다. `walle scan`, `walle check`, `walle diff`는 스캔하는 폴더 안의 `--out` 폴더(그 옆 `cache/` 포함)를 거부하고 종료 코드 2로 끝난다. `walle eve`와 `walle context --compare <apply-id|날짜>`는 재는 폴더에 같은 규칙을 쓴다. `~/.walle` 아래는 예외다.

## scan.json (schema 2)

모든 표면은 `scan.json`을 읽는다. schema 2는 필드를 더하기만 하고 이름을 바꾸지 않는다. schema 1을 읽던 코드는 그대로 돈다.

- `run`: 실행 id(실행 폴더 이름), `parent_id`(같은 루트의 직전 스캔), `label`(`--label`), `walle_version`, `rules_hash`, `estimator`, `counting_revision`, `roots`, `git_heads`
- `items`: 읽거나 고칠 것 하나에 항목 하나. 한 파일의 날짜·모델명 줄(3줄부터), 한 MEMORY.md의 줄, 두 파일 사이에 이어진 복사 줄, 같은 스택의 다른 파일을 통째로 복사한 파일(`file_copy` 노트)은 각각 한 항목이다. 항목 id는 규칙, 원문, 파일 경로의 해시 앞 6자리다. 줄이 옮겨져도, 폴더를 따로 스캔해도 같고, 두 폴더의 같은 줄은 서로 다르다. 경로는 이 기계의 경로라서 다른 곳의 클론은 id가 다르다. 위치에는 잘리지 않은 원문, `end_line`, 해시가 있다. 이어진 복사 줄은 줄마다 위치 하나라서, DELETE가 그 사이의 다른 줄을 가리키지 않는다.
- `triage`: 스택마다 판정, 토큰 구성, 여기부터(`start_here`) 항목 id, 접힌 묶음
- `files[]`: `sha`, `ownership`, `##` 절의 줄 범위도 담는다

토큰 수는 bytes-v2 추정기로 센다. `walle context`와 같은 산문 비율이라서 한 파일은 두 명령에서 같은 수다. Claude Code가 읽는 파일은 HTML 주석 블록을 빼고 센다. Claude Code가 그 주석을 버리기 때문이다. Codex 파일은 통째로 센다. Codex 스킬은 Codex가 목록에 싣는 대로 센다. `agents/openai.yaml`에 `allow_implicit_invocation: false`가 있으면 싣지 않는다. Codex 프로젝트 문서 상한은 `~/.codex/config.toml`의 `project_doc_max_bytes`다(없으면 32 KiB). `--estimator v1`은 bytes-v1(ASCII 4자당 1토큰)로 세고 기본 예산은 8,000이다. HTML 주석 블록을 빼고 세면, 작성자의 기계에서 15,000 bytes-v2 토큰과 8,000 bytes-v1 토큰은 같은 스택을 예산 초과로 판정한다(2026-10-20). 다른 토큰 기준은 bytes-v1 값에 첫 bytes-v2 비율(14,500 / 8,000)을 곱해 두 자리로 반올림한 값이다. 예산이 바뀔 때 그대로 두었다.
claude.ai에서 동기화한 스킬과 플러그인 스킬은 예산 스택에 넣지 않는다. 그 비용은 `walle context`가 보여 준다.
판정(예산 기본 15,000 추정 토큰, `--max-tokens`): 예산을 넘으면 `OVER BUDGET`. 예산 이하이고, 스택(공용 파일 포함)에 확실한 DELETE 줄이 180 토큰(`--estimator v1`은 100 토큰) 이상이거나 충돌(함께 읽히는 달라진 두 줄)이 있거나, 내 파일에 열린 후보가 3개 이상이면 `TRIM`. 나머지는 `LEAN`.
한 스택의 `topic`·`protocol` 쌍은 용어나 메시지만 같고 글은 다르다. 실제 파일에서는 대개 같은 말이다. 그래서 `term_clash` 확인 후보로 나머지 묶음에 두고, 여기부터와 판정에는 넣지 않는다. `walle judge`가 읽는다.
여기부터는 내 파일(폴더의 git 체크아웃과 그 폴더의 MEMORY.md)에서 최대 3개다. 큰 층(MOVE)은 3,600 토큰(`--estimator v1`은 2,000 토큰)을 넘고 스택의 40%를 넘는 내 파일이다. 세션 시작에 읽히는 항목만 판정과 여기부터에 든다.
DELETE는 넓은 쪽 사본을 남긴다. 지울 줄을 읽는 모든 스택이 남길 줄도 읽을 때만 DELETE이고, `~/.claude/CLAUDE.md` 같은 공용 파일의 줄은 지우라고 하지 않는다.
두 실행의 발견 수는 `rules_hash`가 같을 때만, 토큰 합은 `estimator`와 `counting_revision`이 같을 때만 비교한다. `counting_revision` 2는 Claude Code가 읽는 파일을 HTML 주석 블록 없이 센 실행이다. 3은 Codex 스킬 목록도 Codex가 쓰는 대로 센다. 이 값이 없는 실행은 1로 본다. b0f005e와 7a956ce 사이의 실행은 이미 주석을 빼고 셌지만 1로 표시된다. 그때 walle는 공개 전이었다.

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
  - `eve` 하위 에이전트(`walle:eve`)가 함께 깔린다. `walle apply` 뒤에 변경 전후 세션을 비교해서, 첫 호출에서 빠진 원천을 표면별로 보고한다. 읽기만 하고 `haiku` 모델로 돈다. 설명이 모든 세션의 에이전트 목록에 약 200바이트를 더한다.
  - 세션 시작 알람은 기본으로 꺼져 있다. `session_alarm`을 켜고 `budget_tokens`(기본 15,000)를 정하면, 폴더의 스택이 예산을 넘을 때 한 줄을 알려 준다.
  - 알람은 bytes-v2 토큰으로 센다. 예전 bytes-v1 기준으로 `budget_tokens`를 8000으로 저장했다면 15000으로 바꾼다.
- CLI: `pipx install walle-cli` (패키지 이름은 `walle-cli`, 명령은 `walle`)
- Codex와 다른 에이전트: CLI를 pipx로 설치하고 `skills/walle`을 스킬 폴더에 복사
- CI: `walle check . --max-tokens 15000`

## 한계

[README.md](README.md#limits)의 Limits와 같다.
