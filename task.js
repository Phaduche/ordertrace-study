// ============================================================
// VISTA-01 시각 순서 과제 (ORDERTRACE)
// 흐름: 참가자 코드·동의 → 안내 → 연습 1회(피드백) → 본시행 24회(12회 뒤 휴식) → 완료(확인 코드·CSV)
// 자극은 stimuli/manifest.json 의 스프라이트 시트를 캔버스에서 60fps로 1회 재생한다.
// 응답은 서버(/api/ot/*)에 저장하고, 서버가 없어도 이 기기(localStorage)에 남긴다.
// ============================================================

const $ = id => document.getElementById(id);
const card = $("card"), progress = $("progress");
const KEY = "vista01-session";
const BREAK_AFTER = 12;
const OPTIONS = [
  { v: "red", key: "A", label: "빨간 물체가 먼저" },
  { v: "blue", key: "B", label: "파란 물체가 먼저" },
  { v: "same", key: "S", label: "거의 동시에 통과" },
  { v: "unsure", key: "U", label: "순서를 구분할 수 없음" },
];

let MANIFEST = null;
let S = load();   // { code, sid, order:[clip ids], i, practiceDone, responses:[...], startedAt, confirm }

function load() { try { return JSON.parse(localStorage.getItem(KEY)) || null; } catch { return null; } }
function save() { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch {} }
async function api(path, body) {
  try {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), keepalive: true });
    return r.ok ? await r.json() : null;
  } catch { return null; }
}
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clip = id => MANIFEST.clips.find(c => c.id === id);
const shuffle = a => { a = [...a]; for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };

// ── 화면: 동의·코드 ─────────────────────────────────────────
function consent() {
  progress.textContent = "";
  const resume = S && S.code && !S.confirm;
  card.innerHTML = `<h1>시각 순서 판단 과제</h1>
    <p>짧은 영상(약 1초)에서 빨간 물체와 파란 물체가 흰 선을 지나갑니다. 어느 쪽이 먼저 지나갔는지 판단해 주세요.</p>
    ${resume ? `<p>진행 중이던 기록이 있습니다. (코드 ${esc(S.code)}, ${S.i}/${S.order.length} 완료)</p>
      <button id="resume">이어서 진행</button> <button id="discard">진행 기록 지우고 처음부터</button>` : `
    <hr>
    <p>참가자 코드 (연구자에게 받은 것)<br><input type="text" id="code" autocomplete="off"></p>
    <p><label><input type="checkbox" id="agree"> 연구 설명문을 읽었고, 언제든 참여를 그만둘 수 있음을 알고 있습니다. 이름·연락처는 수집하지 않습니다.</label></p>
    <p class="small">이 페이지는 동의 서류를 대신하지 않습니다. 사람 대상 연구 승인 전에는 실제 자료 수집에 쓰지 않습니다.</p>
    <p class="err" id="err"></p>
    <button id="go">다음</button>`}`;
  if (resume) {
    $("resume").onclick = () => S.practiceDone ? (S.i >= S.order.length ? finish() : trial()) : practice(1);
    $("discard").onclick = () => { if (confirm("진행 기록을 지우고 처음부터 시작할까요?")) { S = null; localStorage.removeItem(KEY); consent(); } };
    return;
  }
  $("go").onclick = async () => {
    const code = $("code").value.trim().toUpperCase();
    if (!/^[A-Z0-9]{4,}$/.test(code)) return $("err").textContent = "코드는 영문·숫자 4자 이상이어야 합니다.";
    if (!$("agree").checked) return $("err").textContent = "동의에 체크해 주세요.";
    $("go").disabled = true;
    const mains = MANIFEST.clips.filter(c => c.kind === "main").map(c => c.id);
    const r = await api("/api/ot/start", { code });
    S = { code, sid: r?.sid || null, order: r?.order || shuffle(mains), i: 0, practiceDone: false, responses: [], startedAt: new Date().toISOString(), confirm: null };
    save(); instructions();
  };
}

function instructions() {
  card.innerHTML = `<h1>안내</h1>
    <ol>
      <li>영상은 <b>한 번만</b> 재생됩니다. 재생 전에 3초 카운트다운이 있습니다.</li>
      <li>영상이 끝나면 네 가지 중 하나를 고릅니다. 키보드 A, B, S, U로도 고를 수 있습니다.</li>
      <li>그 다음 얼마나 확신하는지 막대(0~100)를 움직여 표시합니다. 막대를 한 번은 움직여야 합니다.</li>
    </ol>
    <p>먼저 연습을 1번 하고(정답을 알려 줍니다), 이어서 본시행 ${S.order.length}번을 합니다. 본시행에서는 정답을 알려 주지 않습니다. 중간에 한 번 쉬는 화면이 있습니다.</p>
    <button id="go">연습 시작</button>`;
  $("go").onclick = () => practice(1);
}

// ── 재생 ────────────────────────────────────────────────────
function loadSheet(c) {
  return new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = c.sheet; });
}
function playClip(c, canvas) {
  // 60fps 스프라이트 시트를 경과 시간 기준으로 그린다. 건너뛴 프레임 수를 세서 재생 이상 플래그로 쓴다.
  return new Promise(async resolve => {
    const im = await loadSheet(c);
    const ctx = canvas.getContext("2d");
    canvas.width = c.w; canvas.height = c.h;
    let last = -1, skipped = 0, t0 = null;
    const draw = k => { const r = Math.floor(k / c.cols), col = k % c.cols; ctx.drawImage(im, col * c.w, r * c.h, c.w, c.h, 0, 0, c.w, c.h); };
    draw(0);
    const tick = now => {
      if (t0 === null) t0 = now;
      const k = Math.min(c.frames - 1, Math.floor((now - t0) / 1000 * c.fps));
      if (k !== last) { if (last >= 0 && k - last > 1) skipped += k - last - 1; draw(k); last = k; }
      if (k < c.frames - 1) requestAnimationFrame(tick); else resolve({ skipped, duration: now - t0 });
    };
    requestAnimationFrame(tick);
  });
}

async function showClip(c, onDone) {
  card.innerHTML = `<div class="stage"><canvas id="cv"></canvas><div class="count" id="count">3</div></div>
    <p class="small">흰 선을 어느 물체가 먼저 지나는지 보세요. (1회 재생)</p>`;
  const cv = $("cv");
  await loadSheet(c);
  for (const n of [3, 2, 1]) { $("count").textContent = n; await new Promise(r => setTimeout(r, 700)); }
  $("count").style.display = "none";
  const play = await playClip(c, cv);
  onDone(play);
}

// ── 응답 화면 ──────────────────────────────────────────────
function respond(c, play, { kind, onSubmit }) {
  const shown = performance.now();
  let choice = null, conf = null;
  card.innerHTML = `<p>영상이 끝났습니다.</p>
    <h2>어느 물체가 먼저 흰 선을 지났나요?</h2>
    <div class="opts">${OPTIONS.map(o => `<label><input type="radio" name="ans" value="${o.v}"> ${o.label} <span class="small">(${o.key})</span></label>`).join("")}</div>
    <h2>얼마나 확신하나요?</h2>
    <div class="conf"><p id="cval" class="small">막대를 움직여 주세요</p>
      <input type="range" id="conf" min="0" max="100" value="50"><div class="ends"><span>전혀 확신하지 않음</span><span>매우 확신함</span></div></div>
    <p class="err" id="err"></p>
    <button id="next" disabled>${kind === "practice" ? "확인" : "다음"}</button>`;
  const sync = () => { $("next").disabled = !(choice && conf !== null); };
  card.querySelectorAll("input[name=ans]").forEach(el => el.onchange = () => { choice = el.value; sync(); });
  $("conf").oninput = e => { conf = +e.target.value; $("cval").textContent = `확신 정도: ${conf}`; sync(); };
  const onKey = e => { const o = OPTIONS.find(o => o.key === e.key.toUpperCase()); if (o) { const r = card.querySelector(`input[name=ans][value="${o.v}"]`); r.checked = true; r.onchange(); } };
  document.addEventListener("keydown", onKey);
  $("next").onclick = () => {
    document.removeEventListener("keydown", onKey);
    onSubmit({ clip_id: c.id, kind, method: c.method, obs_fps: c.obs_fps, soa_ms: c.soa_ms, profile: c.profile, truth: c.truth,
      response: choice, confidence: conf, rt_ms: Math.round(performance.now() - shown), skipped_frames: play.skipped,
      play_ms: Math.round(play.duration), anomaly: play.skipped > 3 || Math.abs(play.duration - 1000 * c.frames / c.fps) > 150 ? 1 : 0,
      ts: new Date().toISOString() });
  };
}

// ── 연습 ───────────────────────────────────────────────────
function practice(attempt) {
  progress.textContent = "연습";
  const c = MANIFEST.clips.find(x => x.kind === "practice");
  showClip(c, play => respond(c, play, { kind: "practice", onSubmit: async r => {
    r.attempt = attempt; S.responses.push(r); save(); api("/api/ot/response", { sid: S.sid, code: S.code, ...r });
    const ok = r.response === c.truth;
    const name = { red: "빨간 물체", blue: "파란 물체", same: "거의 동시" }[c.truth];
    card.innerHTML = `<p class="${ok ? "ok" : "bad"}">${ok ? "정답입니다." : `틀렸습니다. 정답은 "${name}가 먼저"였습니다.`}</p>
      <p>${ok ? "연습을 마쳤습니다. 본시행에서는 정답을 알려 주지 않습니다." : "한 번 더 연습해 볼까요?"}</p>
      ${ok ? `<button id="go">본시행 시작</button>` : `<button id="go">다시 연습</button> <button id="skip">그냥 본시행 시작</button>`}`;
    $("go").onclick = () => ok ? startMain() : practice(attempt + 1);
    const sk = $("skip"); if (sk) sk.onclick = startMain;
  } }));
}
function startMain() { S.practiceDone = true; S.i = 0; save(); trial(); }

// ── 본시행 ─────────────────────────────────────────────────
function trial() {
  if (S.i >= S.order.length) return finish();
  if (S.i === BREAK_AFTER && !S.breakShown) {
    S.breakShown = true; save();
    card.innerHTML = `<h1>잠시 휴식</h1><p>${BREAK_AFTER}번을 마쳤습니다. 눈을 잠시 쉬고 준비되면 계속해 주세요.</p><button id="go">계속</button>`;
    return $("go").onclick = trial;
  }
  progress.textContent = `${S.i + 1} / ${S.order.length}`;
  const c = clip(S.order[S.i]);
  if (S.i + 1 < S.order.length) loadSheet(clip(S.order[S.i + 1])).catch(() => {});   // 다음 자극 미리 받기
  showClip(c, play => respond(c, play, { kind: "main", onSubmit: async r => {
    r.index = S.i; S.responses.push(r); S.i++; save();
    api("/api/ot/response", { sid: S.sid, code: S.code, ...r });
    trial();
  } }));
}

// ── 완료 ───────────────────────────────────────────────────
async function sha(s) { const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)); return [...new Uint8Array(b)].map(x => x.toString(16).padStart(2, "0")).join(""); }
async function finish() {
  progress.textContent = "";
  card.innerHTML = `<h1>끝났습니다</h1><p>참여해 주셔서 감사합니다. 아래 확인 코드를 연구자에게 보여 주세요.</p><p class="code" id="code">계산 중</p>
    <button id="csv">응답 CSV 저장</button> <button id="purpose">연구 목적 보기</button><div id="why"></div>`;
  if (!S.confirm) {
    const r = await api("/api/ot/finish", { sid: S.sid, code: S.code, n: S.responses.length });
    S.confirm = r?.confirm || (await sha(S.code + S.startedAt + S.responses.map(x => x.response).join(""))).slice(0, 8).toUpperCase();
    save();
  }
  $("code").textContent = S.confirm;
  $("csv").onclick = () => {
    const cols = ["index", "kind", "clip_id", "method", "obs_fps", "soa_ms", "profile", "truth", "response", "confidence", "rt_ms", "skipped_frames", "play_ms", "anomaly", "ts"];
    const rows = [["code", ...cols].join(",")].concat(S.responses.map(r => [S.code, ...cols.map(k => r[k] ?? "")].join(",")));
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob(["﻿" + rows.join("\n")], { type: "text/csv" }));
    a.download = `vista01_${S.code}.csv`; a.click();
  };
  $("purpose").onclick = () => $("why").innerHTML = `<hr><p>이 연구는 낮은 프레임 속도로 찍힌 영상을 <b>관측 프레임 반복</b>으로 되살린 경우와 <b>컴퓨터가 중간 프레임을 생성(보간)</b>한 경우에, 사람이 두 사건의 실제 발생 순서를 다르게 판단하는지 봅니다. 영상마다 재구성 방식·프레임 속도·두 물체의 시간 차가 달랐습니다. 결과는 개인이 아닌 집단 단위로만 분석합니다.</p>`;
}

// ── 시작 ───────────────────────────────────────────────────
fetch("stimuli/manifest.json").then(r => r.json()).then(m => { MANIFEST = m; consent(); })
  .catch(() => { card.innerHTML = `<h1>자극 파일이 없습니다</h1><p>먼저 <code>python3 make_stimuli.py</code> 를 실행해 주세요.</p>`; });
