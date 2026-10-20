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
python3 bin/walle check . --max-tokens 8000  # CI용, 넘으면 종료 코드 1
python3 bin/walle judge --dry-run          # 판정할 항목(파일:줄)과 추정 토큰
python3 bin/walle judge                    # 후보를 배치 파일로 내보냄 → 에이전트가 답 파일을 씀
python3 bin/walle judge --import           # 답을 읽어 judge.md 작성
```

리포트는 `~/.walle/runs/<시각>/`에 쓴다. 점검하는 폴더에는 쓰지 않는다.

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
- CLI: `pipx install walle-cli` (패키지 이름은 `walle-cli`, 명령은 `walle`)
- Codex와 다른 에이전트: CLI를 pipx로 설치하고 `skills/walle`을 스킬 폴더에 복사
- CI: `walle check . --max-tokens 8000`

## 한계

[README.md](README.md#limits)의 Limits와 같다.
