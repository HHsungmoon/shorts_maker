"""로컬 확인용 단일 페이지. 운영 화면은 관리자 페이지(admin-web)다.

여기 두는 이유는 API 가 실제로 도는지 브라우저로 바로 확인하기 위해서다 — Spring 과
admin-web 을 다 띄우지 않고도 파이프라인 상태를 볼 수 있어야 디버깅이 된다.
"""

INDEX_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>shorts_maker</title>
<style>
:root{color-scheme:light dark;--bg:#fff;--fg:#111;--mut:#666;--line:#e3e3e3;--card:#fafafa;--acc:#2563eb}
@media(prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--mut:#999;--line:#2a2a2a;--card:#1a1a1a;--acc:#60a5fa}}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:var(--bg);color:var(--fg);
 font:14px/1.6 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",sans-serif;max-width:1000px;margin-inline:auto}
h1{font-size:18px;margin:0 0 4px} h2{font-size:15px;margin:24px 0 8px}
.mut{color:var(--mut)} .card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin:8px 0}
button{font:inherit;padding:6px 12px;border:1px solid var(--line);border-radius:6px;background:var(--bg);
 color:var(--fg);cursor:pointer}
button:hover{border-color:var(--acc);color:var(--acc)} button:disabled{opacity:.4;cursor:default}
table{border-collapse:collapse;width:100%;font-size:13px} td,th{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line)}
pre{white-space:pre-wrap;font-size:12px;margin:0}
video{width:270px;border-radius:8px;background:#000}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-start}
.warn{border-color:#d97706;background:#d9770615}
code{background:var(--card);padding:1px 4px;border-radius:3px;font-size:12px}
.scroll{max-height:320px;overflow:auto}
</style></head><body>
<h1>shorts_maker <span class="mut" id="health"></span></h1>
<p class="mut">로컬 확인용 화면. 운영 화면은 관리자 페이지에 있다.</p>
<div id="app">불러오는 중…</div>
<script>
const j = (u,o) => fetch(u,o).then(r => r.ok ? r.json() : r.json().then(e => {throw new Error(e.detail||r.status)}));
const fmt = s => `${Math.floor(s/60)}:${String(Math.floor(s%60)).padStart(2,'0')}`;
let poll = null;

async function health(){
  const h = await j('/health');
  document.getElementById('health').textContent =
    `· schema v${h.schema} · whisper ${h.whisperModel} · Gemini ${h.geminiKey?'설정됨':'키 없음'}`;
  return h;
}

async function load(){
  const h = await health();
  const sources = await j('/api/sources');
  const app = document.getElementById('app');
  if(!sources.length){ app.innerHTML = '<div class="card">등록된 원본이 없다. <code>sm source add</code></div>'; return; }
  const parts = [];
  if(!h.geminiKey) parts.push('<div class="card warn">GEMINI_API_KEY 가 없다 — 구간 분할/rank/자르기 단계는 실행할 수 없다.</div>');
  if(h.activeJob) parts.push(`<div class="card">진행 중: <b>${h.activeJob.kind}</b> (${h.activeJob.target}) — ${h.activeJob.status}</div>`);
  for(const s of sources){
    const d = await j(`/api/sources/${s.id}`);
    parts.push(render(d, h));
  }
  app.innerHTML = parts.join('');
  if(h.activeJob && !poll) poll = setTimeout(() => {poll=null; load();}, 3000);
}

function render(d, h){
  const s = d.source;
  const chunks = d.chunks.map(c => `
    <div class="card">
      <b>chunk ${c.id}</b> · ${fmt(c.start_sec)}~${fmt(c.end_sec)} · 발화 ${c.utteranceCount}개
      <div class="row" style="margin-top:8px">
        <button onclick="act('/api/chunks/${c.id}/stt','POST',{force:true})">STT 다시</button>
        <button onclick="act('/api/chunks/${c.id}/segment?force=true','POST')" ${h.geminiKey?'':'disabled'}>구간 분할</button>
        <button onclick="showUtterances(${c.id})">전사 보기</button>
      </div>
      <div id="utt-${c.id}"></div>
      ${c.segments.length ? `<table style="margin-top:8px"><tr><th>구간</th><th>시간</th><th>요약</th></tr>` +
        c.segments.map(g => `<tr><td>[${g.idx}]</td><td>${fmt(g.start_sec)}~${fmt(g.end_sec)}</td>
          <td>${esc(g.description||'')}${g.excluded_by?` <span class="mut">(제외: ${esc(g.excluded_reason||'')})</span>`:''}</td></tr>`).join('') +
        '</table>' : '<p class="mut">구간 없음</p>'}
    </div>`).join('');

  const clips = d.clips.map(c => `
    <div class="card"><div class="row">
      ${c.rendered ? `<video controls preload="metadata" src="/api/clips/${c.id}/file"></video>` : '<div class="mut">미렌더</div>'}
      <div style="flex:1;min-width:220px">
        <b>clip ${c.id}</b> · ${fmt(c.start_sec)}~${fmt(c.end_sec)} (${Math.round(c.end_sec-c.start_sec)}초)
        ${c.score!=null?` · ${c.score}점`:''}
        <p>${esc(c.reason||'')}</p>
        <div class="row">
          ${c.rendered?'':`<button onclick="act('/api/clips/${c.id}/render','POST')">렌더</button>`}
          <button onclick="review(${c.id},'OK')">O</button>
          <button onclick="review(${c.id},'NG')">X</button>
        </div>
        ${c.reviews.length?`<p class="mut">평가: ${c.reviews.map(r=>r.verdict).join(', ')}</p>`:''}
      </div>
    </div></div>`).join('');

  return `<h2>${esc(s.title)}</h2>
    <p class="mut">${s.content_type} · ${fmt(s.duration_sec||0)} · ${esc(s.origin||'')}</p>
    ${chunks}
    <div class="row"><button onclick="rank(${s.id})" ${h.geminiKey?'':'disabled'}>rank 실행</button></div>
    ${d.runs.map(r => `<div class="card"><b>run ${r.id}</b> · ${r.status}
      ${r.criteria_prompt?`<br><span class="mut">기준: ${esc(r.criteria_prompt)}</span>`:''}
      ${r.ranked&&r.ranked.ranked?`<table><tr><th>구간</th><th>점수</th><th>근거</th></tr>`+
        r.ranked.ranked.map(x=>`<tr><td>[${x.idx}]</td><td>${x.score}</td><td>${esc(x.reason)}</td></tr>`).join('')+
        '</table>':''}
      ${r.error?`<p class="mut">${esc(r.error)}</p>`:''}</div>`).join('')}
    <h2>클립</h2>${clips || '<p class="mut">아직 없음</p>'}
    <h2>단계 기록</h2>
    <div class="card scroll"><table><tr><th>단계</th><th>모델</th><th>토큰</th><th>시간</th></tr>
    ${d.stageCalls.map(c=>`<tr><td>${c.stage}${c.error?' ⚠':''}</td><td>${esc(c.model||'-')}</td>
      <td>${c.input_tokens?`in ${c.input_tokens} / out ${c.output_tokens} / think ${c.thinking_tokens??0}`:'-'}</td>
      <td>${c.latency_ms?(c.latency_ms/1000).toFixed(1)+'s':'-'}</td></tr>`).join('')}</table></div>`;
}

const esc = t => String(t).replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));

async function act(url, method, body){
  try{ await j(url,{method,headers:{'content-type':'application/json'},body:body?JSON.stringify(body):null});
       load(); }catch(e){ alert(e.message); }
}
async function rank(id){
  const criteria = prompt('기준 프롬프트 (비우면 기본)', '한 문장으로 인용할 만한 핵심 논지');
  if(criteria===null) return;
  act(`/api/sources/${id}/rank`,'POST',{criteria: criteria||null});
}
async function review(id, verdict){ await act(`/api/clips/${id}/review`,'POST',{verdict}); }
async function showUtterances(id){
  const el = document.getElementById('utt-'+id);
  if(el.innerHTML){ el.innerHTML=''; return; }
  const us = await j(`/api/chunks/${id}/utterances`);
  el.innerHTML = `<div class="scroll" style="margin-top:8px"><pre>${us.map(u=>
    `[${String(u.idx).padStart(3)}] ${fmt(u.start_sec)} ${esc(u.text)}`).join('\\n')}</pre></div>`;
}
load();
</script></body></html>
"""
