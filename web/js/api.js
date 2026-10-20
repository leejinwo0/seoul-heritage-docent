/* =======================================================
   백엔드 API 호출 (같은 도메인의 /api/*)
======================================================= */
async function request(path, options = {}, timeoutMs = 90000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      signal: ctrl.signal,
      ...options,
    });
  } catch (e) {
    throw new Error(
      e.name === "AbortError"
        ? "응답이 너무 오래 걸려 중단했어요. 다시 물어보세요."
        : "서버에 연결하지 못했어요.",
    );
  } finally {
    clearTimeout(timer);
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* 본문 없음 */
  }
  if (!res.ok) {
    const msg =
      body && typeof body.detail === "string"
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
  request("/api/ask", {
    method: "POST",
    body: JSON.stringify({ question, lat, lon }),
  });
