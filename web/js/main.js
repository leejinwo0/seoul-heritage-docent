/* =======================================================
   화면 흐름 (지도 전면 + 대화형 시트)
   1) 설정·지도 로드 → 성곽 구간 선, 장소 표시
   2) 위치 지정 (지도 클릭, 내 위치, 장소 카드) → 가까운 구간 강조, 구간별 추천 질문 교체
   3) 질문 → 대화 기록에 질문·답변·근거 누적, 인용 장소 지도 강조
   4) 모바일: 하단 시트 3단 높이 (끌기 또는 손잡이 탭)
======================================================= */
import * as api from "./api.js";
import { createMap } from "./map.js";

const $ = (id) => document.getElementById(id);
const SEOUL = { lat: [37.4, 37.72], lon: [126.75, 127.2] };
const MOBILE = window.matchMedia("(max-width: 800px)");

// 구간별 추천 질문 (평가 세트로 답변 근거가 확인된 질문 위주)
const SUGGEST = {
  default: ["숭례문은 언제 지어졌어?", "각자성석에는 무엇이 새겨져 있어?", "광희문을 왜 시구문이라고 불렀어?"],
  baegak: ["이 구간은 몇 시까지 들어갈 수 있어?", "숙정문은 왜 닫아 두었어?"],
  naksan: ["낙산공원은 왜 몽마르뜨라고 불려?", "이 구간 코스는 어디서 어디까지야?"],
  heunginjimun: ["흥인지문은 왜 성벽을 하나 더 쌓았어?", "이 구간은 얼마나 걸려?"],
  namsan: ["목멱산 봉수대는 어떻게 신호를 보냈어?", "이 구간 길이는 얼마나 돼?"],
  sungnyemun: ["이 구간에는 왜 성벽이 남아 있지 않아?", "돈의문은 어떻게 사라졌어?"],
  inwangsan: ["인왕산 국사당은 원래 어디에 있었어?", "이 구간은 걷기 힘들어?"],
};

const state = { lat: null, lon: null, segment: null, map: null, asking: false, place: null };

/* ---------- 유틸 ---------- */
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const plainName = (name) => name.replace(/\s*[(（][^)）]*[)）]/g, "").trim();

/** 모델 답변(평문) → 문단 + 출처 번호 버튼. HTML은 모두 이스케이프 후 변환 */
function renderAnswer(text) {
  return escapeHtml(text)
    .split(/\n{2,}/).map((p) => p.trim()).filter(Boolean)
    .map((p) => `<p>${p.replace(/\n/g, "<br>").replace(/\[(\d+)\]/g,
      '<button type="button" class="cite" data-n="$1" aria-label="근거 자료 $1">$1</button>')}</p>`)
    .join("");
}

function renderSources(sources) {
  return sources.map((s) => `
    <li class="src" data-n="${s.n}">
      <span class="src-num" aria-hidden="true">${s.n}</span>
      <div>
        <p class="src-title">${escapeHtml(s.title)}${s.narrative ? '<span class="badge-story">이야기체 해설</span>' : ""}</p>
        <p class="src-meta">${escapeHtml(s.source)}${s.license ? `, ${escapeHtml(s.license)}` : ""}</p>
        ${s.excerpt ? `<p class="src-quote">${escapeHtml(s.excerpt)}…</p>` : ""}
        ${s.audio_url ? `<audio controls preload="none" src="${escapeHtml(s.audio_url)}" aria-label="${escapeHtml(s.title)} 해설 듣기"></audio>` : ""}
      </div>
    </li>`).join("");
}

/* ---------- 시트 (모바일 하단 시트 높이) ---------- */
const sheet = $("sheet");
const ORDER = ["peek", "half", "full"];

function setSheet(next) {
  sheet.dataset.state = next;
}

function ensureOpen() {
  if (MOBILE.matches && sheet.dataset.state === "peek") setSheet("half");
}

function sheetInsets() {
  const r = sheet.getBoundingClientRect();
  return MOBILE.matches ? { bottom: window.innerHeight - r.top } : { left: r.right };
}

function initSheetDrag() {
  const grip = $("grip");
  let startY = 0, startH = 0, moved = false;

  grip.addEventListener("pointerdown", (e) => {
    if (!MOBILE.matches) return;
    startY = e.clientY;
    startH = sheet.getBoundingClientRect().height;
    moved = false;
    grip.setPointerCapture(e.pointerId);
    sheet.classList.add("is-dragging");
  });

  grip.addEventListener("pointermove", (e) => {
    if (!grip.hasPointerCapture(e.pointerId)) return;
    const dy = startY - e.clientY;
    if (Math.abs(dy) > 4) moved = true;
    const h = Math.min(window.innerHeight - 48, Math.max(120, startH + dy));
    sheet.style.setProperty("--sheet-h", `${h}px`);
  });

  grip.addEventListener("pointerup", (e) => {
    if (!grip.hasPointerCapture(e.pointerId)) return;
    grip.releasePointerCapture(e.pointerId);
    sheet.classList.remove("is-dragging");
    const h = sheet.getBoundingClientRect().height;
    sheet.style.removeProperty("--sheet-h");
    if (!moved) {
      // 탭: 다음 높이로 순환
      setSheet(ORDER[(ORDER.indexOf(sheet.dataset.state) + 1) % ORDER.length]);
      return;
    }
    // 끌기: 가장 가까운 높이로 맞춤
    const vh = window.innerHeight;
    const snaps = { peek: 190, half: vh * 0.54, full: vh - 48 };
    const nearest = Object.entries(snaps).sort((a, b) => Math.abs(a[1] - h) - Math.abs(b[1] - h))[0][0];
    setSheet(nearest);
  });
}

/* ---------- 추천 질문 ---------- */
function renderSuggest() {
  const list = state.segment
    ? ["여기는 어떤 곳이야?", ...(SUGGEST[state.segment.segment_id] || [])]
    : SUGGEST.default;
  $("suggest").innerHTML = list
    .map((q) => `<button type="button" class="chip" data-q="${escapeHtml(q)}">${escapeHtml(q)}</button>`)
    .join("");
}

/* ---------- 위치 ---------- */
async function setLocation(lat, lon) {
  state.lat = lat; state.lon = lon;
  state.map.setUser(lat, lon);
  $("btn-clear").hidden = false;
  $("loc").classList.add("is-set");
  $("loc-text").textContent = "위치 확인 중";
  try {
    const seg = await api.getNearest(lat, lon);
    state.segment = seg;
    state.map.lightSegment(seg.segment_id);
    $("loc-text").textContent = `${seg.name}, 성곽까지 ${Math.round(seg.distance_m)}m`;
  } catch {
    state.segment = null;
    $("loc-text").textContent = "위치 지정됨";
  }
  renderSuggest();
}

function clearLocation() {
  Object.assign(state, { lat: null, lon: null, segment: null });
  state.map.setUser(null);
  state.map.lightSegment(null);
  $("btn-clear").hidden = true;
  $("loc").classList.remove("is-set");
  $("loc-text").textContent = "위치 미지정";
  renderSuggest();
}

function useGeolocation() {
  if (!navigator.geolocation) {
    $("loc-text").textContent = "위치 확인 미지원, 지도를 눌러 정하세요";
    return;
  }
  $("loc-text").textContent = "현재 위치 확인 중";
  navigator.geolocation.getCurrentPosition(
    ({ coords }) => {
      const { latitude: lat, longitude: lon } = coords;
      const ok = lat >= SEOUL.lat[0] && lat <= SEOUL.lat[1] && lon >= SEOUL.lon[0] && lon <= SEOUL.lon[1];
      if (!ok) { $("loc-text").textContent = "서울 밖이에요, 지도를 눌러 정하세요"; return; }
      setLocation(lat, lon);
    },
    () => { $("loc-text").textContent = "위치 권한 없음, 지도를 눌러 정하세요"; },
    { enableHighAccuracy: true, timeout: 10000 },
  );
}

/* ---------- 장소 카드 ---------- */
function openPlace(place) {
  state.place = place;
  state.map.selectPlace(place.id);
  $("place-name").textContent = place.name;
  $("place-card").hidden = false;
  ensureOpen();
}

function closePlace() {
  state.place = null;
  state.map.selectPlace(null);
  $("place-card").hidden = true;
}

/* ---------- 질문·답변 ---------- */
function addTurn(question) {
  $("intro")?.remove();
  const ctx = state.segment ? `${state.segment.name} 기준` : "위치 없이";
  const turn = document.createElement("article");
  turn.className = "turn";
  turn.innerHTML = `
    <div class="q"><p class="q-text">${escapeHtml(question)}</p><span class="q-ctx">${escapeHtml(ctx)}</span></div>
    <div class="a is-pending"><span class="pending-mark" aria-hidden="true"><i></i><i></i><i></i></span>
      ${escapeHtml(state.segment ? `${state.segment.name} 자료를 찾아 답을 쓰는 중` : "자료를 찾아 답을 쓰는 중")}</div>`;
  $("thread").appendChild(turn);
  turn.scrollIntoView({ block: "start" });
  return turn;
}

function fillTurn(turn, res) {
  const a = turn.querySelector(".a");
  a.className = "a";
  a.innerHTML = renderAnswer(res.answer);
  const sources = res.sources || [];
  if (sources.length) {
    const det = document.createElement("details");
    det.className = "srcs";
    det.innerHTML = `<summary>근거 자료 ${sources.length}개</summary><ol class="src-list">${renderSources(sources)}</ol>`;
    turn.appendChild(det);
  }
  turn._sources = sources;
}

function failTurn(turn, question, message) {
  const a = turn.querySelector(".a");
  a.className = "a";
  a.innerHTML = `<p class="a-error">${escapeHtml(message)}<button type="button" class="retry">다시 묻기</button></p>`;
  a.querySelector(".retry").addEventListener("click", () => { turn.remove(); ask(question); });
}

async function ask(question) {
  const q = question.trim();
  if (!q || state.asking) return;
  state.asking = true;
  $("btn-send").disabled = true;
  $("q").value = "";
  autosize();
  ensureOpen();
  const turn = addTurn(q);
  try {
    const res = await api.ask(q, state.lat, state.lon);
    fillTurn(turn, res);
    state.map.highlightPlaces(res.places || []);
  } catch (e) {
    failTurn(turn, q, e.message || "답변을 받지 못했어요.");
  } finally {
    state.asking = false;
    $("btn-send").disabled = false;
  }
}

/** 출처 번호 클릭 → 근거 목록 펼침, 해당 항목 강조, 장소가 있으면 지도 이동 */
function onCite(btn) {
  const turn = btn.closest(".turn");
  const det = turn.querySelector(".srcs");
  if (!det) return;
  det.open = true;
  turn.querySelectorAll(".src.is-target").forEach((el) => el.classList.remove("is-target"));
  const li = det.querySelector(`.src[data-n="${btn.dataset.n}"]`);
  if (li) { li.classList.add("is-target"); li.scrollIntoView({ block: "nearest" }); }
  const src = (turn._sources || []).find((s) => String(s.n) === btn.dataset.n);
  if (src?.place_id) state.map.focusPlace(src.place_id);
}

function autosize() {
  const t = $("q");
  t.style.height = "auto";
  t.style.height = `${Math.min(t.scrollHeight, 120)}px`;
}

/* ---------- 초기화 ---------- */
async function init() {
  initSheetDrag();
  renderSuggest();

  let config = {};
  try { config = await api.getConfig(); } catch { /* 키 없이 대체 지도 */ }

  state.map = await createMap($("map"), { seoulMapKey: config.seoul_map_key, getInsets: sheetInsets });
  if (state.map.provider !== "smart-seoul-map") {
    $("map-note").textContent = "대체 지도(OpenStreetMap) 표시 중";
    $("map-note").hidden = false;
  }

  try {
    const [segments, places] = await Promise.all([api.getSegments(), api.getPlaces()]);
    state.map.drawSegments(segments);
    state.map.drawPlaces(places);
  } catch (e) {
    $("loc-text").textContent = `지도 데이터 로드 실패: ${e.message}`;
  }

  state.map.onClick((lat, lon) => { closePlace(); setLocation(lat, lon); });
  state.map.onPlaceClick(openPlace);

  $("btn-geo").addEventListener("click", useGeolocation);
  $("btn-clear").addEventListener("click", clearLocation);
  $("place-close").addEventListener("click", closePlace);
  $("place-stand").addEventListener("click", async () => {
    const p = state.place;
    closePlace();
    await setLocation(p.lat, p.lon);
    ask("여기는 어떤 곳이야?");
  });
  $("place-ask").addEventListener("click", () => {
    const p = state.place;
    closePlace();
    ask(`${plainName(p.name)}에 대해 알려줘`);
  });

  $("suggest").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (chip) ask(chip.dataset.q);
  });
  $("thread").addEventListener("click", (e) => {
    const cite = e.target.closest(".cite");
    if (cite) onCite(cite);
  });

  $("composer").addEventListener("submit", (e) => { e.preventDefault(); ask($("q").value); });
  $("q").addEventListener("input", autosize);
  $("q").addEventListener("focus", ensureOpen);
  $("q").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask($("q").value); }
  });
}

init();
