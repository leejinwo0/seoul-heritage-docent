/* =======================================================
   백엔드 API 호출 (같은 도메인의 /api/*)
======================================================= */
async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let body = null;
  try { body = await res.json(); } catch { /* 본문 없음 */ }
  if (!res.ok) {
    const msg = (body && body.detail && typeof body.detail === "string")
      ? body.detail
      : `요청 실패 (${res.status})`;
    throw new Error(msg);
  }
  return body;
}

export const getConfig = () => request("/api/config");
export const getSegments = () => request("/api/segments");
export const getPlaces = () => request("/api/places");
export const getNearest = (lat, lon) =>
  request(`/api/nearest?lat=${lat}&lon=${lon}`);
export const ask = (question, lat, lon) =>
  request("/api/ask", { method: "POST", body: JSON.stringify({ question, lat, lon }) });
