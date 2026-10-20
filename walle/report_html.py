"""Render a scan as one self-contained HTML page with filters."""

from __future__ import annotations

import json

PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>walle Instruction Report</title>
<style>
:root { --bg:#f6f4ef; --fg:#1f1d1a; --muted:#6b665d; --card:#fffdf8; --line:#e2ddd2;
  --bar:#c98a1b; --c1:#b5562b; --c3:#5b6fb5; --c4:#6c8a3a; --drift:#a23b72; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#171614; --fg:#ece8e0; --muted:#a39d92; --card:#211f1c; --line:#38342e;
  --bar:#e0a43a; --c1:#e07a4f; --c3:#8b9ce0; --c4:#9cbc66; --drift:#d46aa3; } }
:root[data-theme="dark"] { --bg:#171614; --fg:#ece8e0; --muted:#a39d92; --card:#211f1c; --line:#38342e;
  --bar:#e0a43a; --c1:#e07a4f; --c3:#8b9ce0; --c4:#9cbc66; --drift:#d46aa3; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; }
main { max-width:1100px; margin:0 auto; padding:24px 16px 64px; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:17px; margin:32px 0 8px; }
.muted { color:var(--muted); font-size:13px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; margin:8px 0; }
.row { display:grid; grid-template-columns:minmax(0,2.2fr) 70px minmax(0,3fr) 64px; gap:10px; align-items:center;
  padding:6px 0; border-bottom:1px solid var(--line); font-size:13px; }
.row span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.bar { height:10px; background:var(--bar); border-radius:3px; }
.controls { display:flex; flex-wrap:wrap; gap:8px; margin:8px 0; }
select, input { font:inherit; padding:6px 8px; border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--fg); }
input { flex:1; min-width:180px; }
.tag { display:inline-block; font-size:12px; padding:1px 7px; border-radius:99px; color:#fff; margin-right:6px; }
.t1 { background:var(--c1); } .t3 { background:var(--c3); } .t4 { background:var(--c4); }
.tdrift,.ttopic,.tprotocol,.tduplicate,.trepeat { background:var(--drift); }
code { font-size:12px; word-break:break-all; }
.text { margin-top:4px; font-size:13px; }
@media (max-width:640px) { .row { grid-template-columns:minmax(0,1fr) 60px; } .row .wide { display:none; } }
</style>
</head>
<body>
<main>
<h1>walle 지시 문서 리포트</h1>
<div class="muted" id="meta"></div>

<h2>세션 시작 스택</h2>
<div class="muted">그 폴더에서 세션을 열 때 첫 프롬프트 전에 읽히는 지시. 토큰은 추정치.</div>
<div class="controls"><select id="agent"><option value="all">모든 에이전트</option><option>claude</option><option>codex</option></select></div>
<div class="card" id="stacks"></div>

<h2>발견</h2>
<div class="controls">
<select id="cause"><option value="all">모든 원인</option><option value="1">원인 1 무관 비용 후보</option>
<option value="3">원인 3 모호한 품질</option><option value="4">원인 4 구모델 보정</option>
<option value="drift">drift (충돌 후보)</option><option value="topic">topic (충돌 후보)</option>
<option value="protocol">protocol (충돌 후보)</option><option value="duplicate">duplicate</option><option value="repeat">repeat</option></select>
<input id="q" placeholder="파일이나 글자로 거르기">
</div>
<div class="muted" id="count"></div>
<div id="findings"></div>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const home = (p) => p ? p.replace(/^\\/home\\/[^/]+|^\\/Users\\/[^/]+/, '~') : '-';
const esc = (s) => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
document.getElementById('meta').textContent =
  `생성 ${D.generated} · 수거 ${D.artifact_count}개 (워크트리 사본 ${D.copy_count}개) · 발견 ${D.flags.length + D.pairs.length}건`;
function drawStacks() {
  const a = document.getElementById('agent').value;
  const rows = D.stacks.filter(s => a === 'all' || s.agent === a).sort((x, y) => y.tokens_est - x.tokens_est);
  const max = Math.max(1, ...rows.map(s => s.tokens_est));
  document.getElementById('stacks').innerHTML = rows.map(s =>
    `<div class="row"><span title="${esc(s.folder)}">${esc(home(s.folder))}</span><span>${s.agent}</span>
     <span class="wide"><div class="bar" style="width:${(100 * s.tokens_est / max).toFixed(1)}%"></div></span>
     <span>${s.tokens_est}</span></div>`).join('');
}
function drawFindings() {
  const c = document.getElementById('cause').value, q = document.getElementById('q').value.toLowerCase();
  const items = [
    ...D.flags.map(f => ({key: String(f.cause), tag: `원인 ${f.cause}`, cls: 't' + f.cause,
      where: `${home(f.file)}:${f.line}`, text: `[${f.match}] ${f.text}`, note: f.kind + (f.candidate ? ' · 후보' : '')})),
    ...D.pairs.map(p => ({key: p.kind, tag: p.kind, cls: 't' + p.kind,
      where: `${home(p.a.file)}:${p.a.line} ↔ ${home(p.b.file)}:${p.b.line}`,
      text: `A: ${p.a.text}\\nB: ${p.b.text}`,
      note: p.shared ? `공유: ${p.shared.join(', ')}` + (p.opposite ? ` · 부정이 갈린 말: ${p.opposite.join(', ')}` : '')
        : `유사도 ${p.score}`})),
  ].filter(i => (c === 'all' || i.key === c) && (!q || (i.where + i.text).toLowerCase().includes(q)));
  document.getElementById('count').textContent = `${items.length}건`;
  document.getElementById('findings').innerHTML = items.slice(0, 400).map(i =>
    `<div class="card"><span class="tag ${i.cls}">${esc(i.tag)}</span><span class="muted">${esc(i.note)}</span>
     <div><code>${esc(i.where)}</code></div><div class="text">${esc(i.text).replace(/\\n/g, '<br>')}</div></div>`).join('');
}
for (const id of ['agent']) document.getElementById(id).addEventListener('change', drawStacks);
for (const id of ['cause', 'q']) document.getElementById(id).addEventListener('input', drawFindings);
drawStacks(); drawFindings();
</script>
</body>
</html>
"""


def render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return PAGE.replace("__DATA__", payload)

