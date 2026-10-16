/* =======================================================
   화면 흐름
   1) 설정·지도 로드 → 성곽 구간 선, 장소 표시
   2) 위치 지정 (지도 클릭 또는 현재 위치) → 가장 가까운 구간 강조
   3) 질문 → 답변·근거 자료 표시, 인용 장소 지도 강조
======================================================= */
import * as api from "./api.js";
import { createMap } from "./map.js";

const $ = (id) => document.getElementById(id);
const SEOUL = { lat: [37.40, 37.72], lon: [126.75, 127.20] };

const state = { lat: null, lon: null, map: null, asking: false };

/* ---------- 유틸 ---------- */
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

/** 모델 답변(평문) → 문단 + 출처 번호 링크. HTML은 모두 이스케이프 후 변환 */
function renderAnswer(text) {
  return escapeHtml(text)
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => `<p>${p.replace(/\n/g, "<br>")
      .replace(/\[(\d+)\]/g, '<a class="cite" href="#src-$1" data-n="$1" aria-label="근거 자료 $1">$1</a>')}</p>`)
    .join("");
}

function showError(msg) {
  $("error").textContent = msg;
  $("error").hidden = !msg;
}

function inSeoul(lat, lon) {
  return lat >= SEOUL.lat[0] && lat <= SEOUL.lat[1] && lon >= SEOUL.lon[0] && lon <= SEOUL.lon[1];
}

/* ---------- 위치 ---------- */
async function setLocation(lat, lon) {
  state.lat = lat; state.lon = lon;
  state.map.setUser(lat, lon);
  $("btn-clear").hidden = false;
  $("where-text").textContent = "위치 확인 중";
  try {
    const seg = await api.getNearest(lat, lon);
    state.map.lightSegment(seg.segment_id);
    $("where-text").textContent = `${seg.name} 근처, 성곽까지 약 ${Math.round(seg.distance_m)}m`;
    $("where").classList.add("is-set");
  } catch (e) {
    $("where-text").textContent = "위치를 정했어요. 가까운 구간은 찾지 못했어요.";
  }
}

function clearLocation() {
  state.lat = state.lon = null;
  state.map.setUser(null);
  state.map.lightSegment(null);
  $("btn-clear").hidden = true;
  $("where").classList.remove("is-set");
  $("where-text").textContent = "지도를 눌러 서 있을 위치를 정하세요.";
}

function useGeolocation() {
  if (!navigator.geolocation) {
    showError("이 브라우저는 위치 확인을 지원하지 않아요. 지도를 눌러 위치를 정하세요.");
    return;
  }
  $("where-text").textContent = "현재 위치 확인 중";
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const { latitude: lat, longitude: lon } = pos.coords;
      if (!inSeoul(lat, lon)) {
        $("where-text").textContent = "현재 위치가 서울 밖이에요. 지도를 눌러 위치를 정하세요.";
        return;
      }
      showError("");
      setLocation(lat, lon);
    },
    () => { $("where-text").textContent = "위치 권한이 없어요. 지도를 눌러 위치를 정하세요."; },
    { enableHighAccuracy: true, timeout: 10000 },
  );
}

/* ---------- 질문 ---------- */
function renderSources(sources) {
  $("sources").innerHTML = sources.map((s) => `
    <li class="src" id="src-${s.n}">
      <span class="src-num" aria-hidden="true">${s.n}</span>
      <div class="src-body">
        <p class="src-title">${escapeHtml(s.title)}${s.narrative ? '<span class="badge-story">이야기체 해설</span>' : ""}</p>
        <p class="src-meta">${escapeHtml(s.source || "")}${s.license ? `, ${escapeHtml(s.license)}` : ""}</p>
        ${s.excerpt ? `<details><summary>원문 일부 보기</summary><blockquote>${escapeHtml(s.excerpt)}…</blockquote></details>` : ""}
        ${s.audio_url ? `<audio controls preload="none" src="${escapeHtml(s.audio_url)}" aria-label="${escapeHtml(s.title)} 해설 듣기"></audio>` : ""}
      </div>
    </li>`).join("");
  $("sources").previousElementSibling.hidden = sources.length === 0;
}

async function submitQuestion(question) {
  const q = question.trim();
  if (!q || state.asking) return;
  state.asking = true;
  showError("");
  $("btn-ask").disabled = true;
  $("btn-ask").textContent = "답을 찾는 중";
  $("result").hidden = false;
  $("answer").className = "answer is-loading";
  $("answer").textContent = "자료를 찾아 답을 만들고 있어요.";
  $("sources").innerHTML = "";

  try {
    const res = await api.ask(q, state.lat, state.lon);
    $("answer").className = "answer";
    $("answer").innerHTML = renderAnswer(res.answer);
    renderSources(res.sources || []);
    state.map.highlightPlaces(res.places || []);
  } catch (e) {
    $("result").hidden = true;
    showError(e.message || "답변을 받지 못했어요. 잠시 후 다시 물어보세요.");
  } finally {
    state.asking = false;
    $("btn-ask").disabled = false;
    $("btn-ask").textContent = "물어보기";
  }
}

/* ---------- 초기화 ---------- */
async function init() {
  let config = {};
  try { config = await api.getConfig(); } catch { /* 키 없이 대체 지도 */ }

  state.map = await createMap($("map"), { seoulMapKey: config.seoul_map_key });
  if (state.map.provider !== "smart-seoul-map") {
    $("map-note").textContent = "대체 지도(OpenStreetMap) 표시 중";
    $("map-note").hidden = false;
  }

  try {
    const [segments, places] = await Promise.all([api.getSegments(), api.getPlaces()]);
    state.map.drawSegments(segments);
    state.map.drawPlaces(places);
  } catch (e) {
    showError(`지도 데이터를 불러오지 못했어요: ${e.message}`);
  }

  state.map.onClick((lat, lon) => setLocation(lat, lon));
  $("btn-geo").addEventListener("click", useGeolocation);
  $("btn-clear").addEventListener("click", clearLocation);

  $("ask-form").addEventListener("submit", (e) => {
    e.preventDefault();
    submitQuestion($("q").value);
  });
  $("q").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      submitQuestion($("q").value);
    }
  });
  document.querySelectorAll(".chip").forEach((chip) =>
    chip.addEventListener("click", () => {
      $("q").value = chip.dataset.q;
      submitQuestion(chip.dataset.q);
    }));

  // 출처 번호 클릭 → 해당 근거 자료 강조
  $("answer").addEventListener("click", (e) => {
    const a = e.target.closest(".cite");
    if (!a) return;
    document.querySelectorAll(".src.is-target").forEach((el) => el.classList.remove("is-target"));
    const target = $(`src-${a.dataset.n}`);
    if (target) target.classList.add("is-target");
  });
}

init();
