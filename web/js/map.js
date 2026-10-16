/* =======================================================
   지도 어댑터
   - 기본: 스마트서울맵 OpenAPI V5 (Leaflet 확장, EPSG:5179) + 경성대지도 WMS
   - 대체: 키 없음·로드 실패 시 OpenStreetMap (EPSG:3857)
   - 지도 라이브러리 의존 코드는 이 파일에만 둠 → 지도 교체 시 이 파일만 수정
   - 진단: 주소 끝에 ?debug 추가 시 줌 레벨별 타일 요청·성공·실패 수 표시
======================================================= */
const SSM = "https://map.seoul.go.kr/openapi/v5";
const WMS_URL =
  "https://map.seoul.go.kr/smgis2/proxy?url=prop/tileMapGeoserver/wms?";
const WMS_GYEONGSEONG = "tile_map:g_old_capital_tms";
const LEAFLET_CDN = "https://unpkg.com/leaflet@1.9.4/dist";

// 스마트서울맵 초기 화면: 한양도성 중심 (스토리맵과 동일하게 타일 추가 전 화면 지정)
const SSM_CENTER = [37.572, 126.993];
const SSM_START_ZOOM = 7;
const SSM_MAX_ZOOM = 11;

const DEBUG = new URLSearchParams(location.search).has("debug");

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error(`스크립트 로드 실패: ${src}`));
    document.head.appendChild(s);
  });
}

function loadCss(href) {
  return new Promise((resolve) => {
    const l = document.createElement("link");
    l.rel = "stylesheet";
    l.href = href;
    l.onload = resolve;
    l.onerror = resolve; // CSS 실패는 지도 동작을 막지 않음
    document.head.appendChild(l);
  });
}

/** 스마트서울맵 스크립트 로드. 성공 시 true, 실패 시 false (대체 지도 사용) */
async function loadSeoulMap(key) {
  if (!key) return false;
  try {
    await loadCss(`${SSM}/${key}/public/map/css/5.0`);
    await loadScript(`${SSM}/${key}/public/map/js/5.0`);
    await loadScript(`${SSM}/${key}/public/map/base/js/5179/5.0`);
    return (
      typeof getCrsEx === "function" && !!(window.L && L.TileLayer.DAWULGIS_EX)
    );
  } catch (e) {
    console.warn(e.message);
    return false;
  }
}

/** 스마트서울맵 미사용 시 Leaflet 단독 로드 (스마트서울맵 스크립트에 Leaflet 포함) */
async function ensureLeaflet() {
  if (window.L) return;
  await loadCss(`${LEAFLET_CDN}/leaflet.css`);
  await loadScript(`${LEAFLET_CDN}/leaflet.js`);
}

/** ?debug 모드: 줌 레벨별 타일 요청·성공·실패 수를 지도 좌하단에 표시 */
function attachTileDebug(map, layer) {
  const note = document.getElementById("map-note");
  if (!note) return;
  const stats = {};
  const show = () => {
    const z = map.getZoom();
    const s = stats[z] || { req: 0, ok: 0, err: 0 };
    note.hidden = false;
    note.textContent = `zoom ${z}, 요청 ${s.req}, 성공 ${s.ok}, 실패 ${s.err}`;
  };
  const count = (key) => () => {
    const z = map.getZoom();
    stats[z] = stats[z] || { req: 0, ok: 0, err: 0 };
    stats[z][key]++;
    show();
  };
  layer.on("tileloadstart", count("req"));
  layer.on("tileload", count("ok"));
  layer.on("tileerror", count("err"));
  map.on("zoomend", show);
  show();
}

export async function createMap(el, { seoulMapKey }) {
  const useSeoul = await loadSeoulMap(seoulMapKey);
  await ensureLeaflet();

  let map,
    baseLayer,
    oldLayer = null,
    detailZoom;

  if (useSeoul) {
    map = L.map(el, {
      crs: getCrsEx(),
      zoomControl: false,
      doubleClickZoom: false, // 위치 지정 클릭 시 확대 방지
      zoomAnimation: false, // 확대 애니메이션 중 타일 레벨 전환 누락 방지
      minZoom: 5,
      maxZoom: SSM_MAX_ZOOM,
    }).setView(SSM_CENTER, SSM_START_ZOOM);

    baseLayer = new L.TileLayer.DAWULGIS_EX(
      `${SSM}/${seoulMapKey}/public/map/base/dawul_kor_normal/{z}/{j}/{k}/{x}/{y}/png`,
      { minZoom: 1, maxZoom: 15 },
    );
    oldLayer = L.tileLayer.wms(WMS_URL, {
      layers: WMS_GYEONGSEONG,
      format: "image/png",
      transparent: true,
      maxZoom: 18,
      attribution: "경성대지도",
    });
    detailZoom = SSM_MAX_ZOOM;
  } else {
    map = L.map(el, { zoomControl: false, doubleClickZoom: false }).setView(
      SSM_CENTER,
      14,
    );
    baseLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    });
    detailZoom = 16;
  }

  baseLayer.addTo(map);
  if (DEBUG) attachTileDebug(map, baseLayer);
  L.control.zoom({ position: "bottomright" }).addTo(map);
  if (oldLayer) addLayerToggle(map, oldLayer);

  // 컨테이너 크기 변화(모바일 회전·패널 높이 변화) 시 타일 재계산
  new ResizeObserver(() => map.invalidateSize()).observe(el);

  const wallLayer = L.layerGroup().addTo(map);
  const litLayer = L.layerGroup().addTo(map);
  const placeLayer = L.layerGroup().addTo(map);
  const placeMarkers = new Map();
  let segmentsFc = null;
  let userMarker = null;

  return {
    provider: useSeoul ? "smart-seoul-map" : "openstreetmap",

    /** 성곽 구간 선: 짙은 테두리 + 성돌 색 이중선, 전체 범위로 화면 맞춤 */
    drawSegments(fc) {
      segmentsFc = fc;
      wallLayer.clearLayers();
      const casing = L.geoJSON(fc, {
        style: { className: "wall-casing", weight: 9, interactive: false },
      });
      const line = L.geoJSON(fc, {
        style: { className: "wall-line", weight: 4.5 },
        onEachFeature: (f, layer) =>
          layer.bindTooltip(f.properties.name, {
            sticky: true,
            className: "place-tip",
          }),
      });
      casing.addTo(wallLayer);
      line.addTo(wallLayer);
      map.fitBounds(line.getBounds(), {
        padding: [24, 24],
        maxZoom: detailZoom,
      });
    },

    /** 좌표가 있는 장소 점 표시 */
    drawPlaces(fc) {
      placeLayer.clearLayers();
      placeMarkers.clear();
      fc.features.forEach((f) => {
        const [lon, lat] = f.geometry.coordinates;
        const m = L.circleMarker([lat, lon], {
          radius: 4.5,
          weight: 1.5,
          color: "#20262a",
          fillColor: "#f1f2ef",
          fillOpacity: 1,
        }).bindTooltip(f.properties.name, {
          className: "place-tip",
          direction: "top",
          offset: [0, -4],
        });
        m.addTo(placeLayer);
        placeMarkers.set(f.properties.place_id, m);
      });
    },

    /** 현재 구간 강조 (segment_id 또는 null) */
    lightSegment(segmentId) {
      litLayer.clearLayers();
      if (!segmentsFc || !segmentId) return;
      const feat = segmentsFc.features.find(
        (f) => f.properties.segment_id === segmentId,
      );
      if (feat)
        L.geoJSON(feat, {
          style: { className: "wall-lit", weight: 6, interactive: false },
        }).addTo(litLayer);
    },

    /** 내 위치 마커 (lat이 null이면 제거) */
    setUser(lat, lon) {
      if (userMarker) userMarker.remove();
      userMarker = null;
      if (lat == null) return;
      userMarker = L.circleMarker([lat, lon], {
        radius: 8,
        weight: 3,
        color: "#ffffff",
        fillColor: "#e3a33a",
        fillOpacity: 1,
      }).addTo(map);
    },

    /** 답변에 인용된 장소 강조. 좌표가 있는 장소만 지도에 표시 가능 */
    highlightPlaces(placeIds) {
      placeMarkers.forEach((m) =>
        m.setStyle({ radius: 4.5, fillColor: "#f1f2ef", color: "#20262a" }),
      );
      const hits = placeIds.map((id) => placeMarkers.get(id)).filter(Boolean);
      hits.forEach((m) => {
        m.setStyle({ radius: 7, fillColor: "#2e5242", color: "#ffffff" });
        m.bringToFront();
      });
      if (userMarker) userMarker.bringToFront();
      if (hits.length) {
        const pts = hits.map((m) => m.getLatLng());
        if (userMarker) pts.push(userMarker.getLatLng());
        // 확대 애니메이션 비활성화 상태와 맞춰 즉시 이동
        map.fitBounds(L.latLngBounds(pts), {
          padding: [60, 60],
          maxZoom: detailZoom,
        });
      }
      return hits.length;
    },

    onClick(handler) {
      map.on("click", (e) => handler(e.latlng.lat, e.latlng.lng));
    },
  };
}

/** 지금 지도 ↔ 경성대지도 전환 버튼 (경성대지도는 배경 위 중첩) */
function addLayerToggle(map, oldLayer) {
  const Toggle = L.Control.extend({
    options: { position: "topright" },
    onAdd() {
      const box = L.DomUtil.create("div", "layer-toggle");
      box.innerHTML = `
        <button type="button" data-layer="now" aria-pressed="true">지금 지도</button>
        <button type="button" data-layer="old" aria-pressed="false">경성대지도</button>`;
      L.DomEvent.disableClickPropagation(box);
      box.addEventListener("click", (e) => {
        const btn = e.target.closest("button");
        if (!btn) return;
        const old = btn.dataset.layer === "old";
        if (old && !map.hasLayer(oldLayer)) map.addLayer(oldLayer);
        if (!old && map.hasLayer(oldLayer)) map.removeLayer(oldLayer);
        box
          .querySelectorAll("button")
          .forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
      });
      return box;
    },
  });
  map.addControl(new Toggle());
}
