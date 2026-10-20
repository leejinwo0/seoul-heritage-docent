"""
app/generator.py
검색된 청크를 근거로 Gemini가 도슨트 답변을 만들고, 출처 번호를 실제 문서와 연결합니다.

설계 원칙
- 근거 한정 : 제공된 자료에 있는 내용만 답하고, 없으면 모른다고 말함
- 출처 인용 : 문장 끝에 [1], [2] 번호 → 응답의 sources 목록과 연결
- 극화 주의 : style=narrative(오디오 해설 등 이야기체) 자료는 "해설에 따르면"처럼 전하고
              대사·심리 묘사는 사실로 단정하지 않음
- 위치 활용 : 현재 구간과 장소까지 거리를 알려줘서 "여기서 약 120m" 같은 안내가 가능
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

from app import retrieval as R

# AI Studio에서 무료로 쓸 수 있는 모델 이름으로 바꿔 쓰세요 (.env의 GEMINI_MODEL이 우선)
DEFAULT_MODEL = "gemini-3.8-flash"
API = "https://generativelanguage.googleapis.com/v1beta"

SYSTEM_PROMPT = """당신은 서울 한양도성을 안내하는 역사 도슨트입니다.

규칙
1. 아래 [자료]에 있는 내용만 근거로 답하세요. 자료에 없는 사실, 연도, 인물은 지어내지 마세요.
2. 자료로 답할 수 없으면 "제가 가진 자료에는 그 내용이 없어요"라고 솔직하게 말하고,
   자료에 있는 관련 내용이 있으면 대신 안내하세요.
3. 근거로 쓴 문장 끝에 자료 번호를 [1], [2]처럼 붙이세요. 번호는 [자료]에 있는 것만 쓰세요.
4. '이야기체' 표시가 있는 자료는 극화된 서술이 섞여 있습니다. 내용을 전할 때는
   "해설에 따르면"처럼 출처를 밝히고, 대사나 인물의 속마음은 사실처럼 단정하지 마세요.
5. [사용자 위치]의 거리는 성곽 선까지의 거리일 뿐, 특정 장소까지의 거리가 아닙니다.
   장소까지의 거리는 자료에 '현재 위치에서 약 ○○m'가 적혀 있을 때만 "지금 계신 곳에서 약 ○○m"처럼 말하고,
   없으면 "이 근처에 있는" 처럼 거리 없이 표현하세요.
6. 현장에서 듣는 해설처럼 친근한 존댓말로, 3~6문장 정도로 간결하게 답하세요."""


def model_name() -> str:
    return os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL


def build_context(chunks: list[dict], segment: dict | None) -> str:
    lines = []
    if segment:
        lines.append(
            f"[사용자 위치] 가장 가까운 성곽 구간은 {segment['name']}이며, "
            f"성곽 선까지 약 {segment['distance_m']:.0f}m (특정 장소까지의 거리 아님)"
        )
        lines.append("")
    lines.append("[자료]")
    for i, c in enumerate(chunks, 1):
        tags = [c["source"] or ""]
        if c.get("distance_m") is not None:
            tags.append(f"현재 위치에서 약 {c['distance_m']:.0f}m")
        if c.get("style") == "narrative":
            tags.append("이야기체")
        lines.append(f"[{i}] {c['title']} ({', '.join(t for t in tags if t)})")
        lines.append(c["content"])
        lines.append("")
    return "\n".join(lines)


def call_gemini(system: str, user: str, temperature: float = 0.3) -> str:
    url = f"{API}/models/{model_name()}:generateContent"
    body = json.dumps(
        {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": 2048},
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": os.environ["GEMINI_API_KEY"],
        },
    )

    data = None
    for attempt in range(3):
        try:
            # 요청 1회 최대 25초 → 재시도 포함 전체 대기 약 1분 이내
            with urllib.request.urlopen(req, timeout=25) as res:
                data = json.load(res)
            break
        except urllib.error.HTTPError as e:
            # 429: 사용량 한도, 500/503: 서버 일시 과부하 → 점점 길게 기다리며 재시도
            if e.code in (429, 500, 503) and attempt < 2:
                wait = 3 * (2**attempt)  # 3초 → 6초
                print(f"  Gemini 응답 지연({e.code}) → {wait}초 후 재시도")
                time.sleep(wait)
                continue
            raise RuntimeError(
                f"Gemini API 오류 {e.code}: {e.read().decode()[:300]}"
            ) from e
        except (TimeoutError, urllib.error.URLError) as e:
            # 응답 시간 초과·연결 끊김 → 재시도
            if attempt < 2:
                print(f"  Gemini 응답 시간 초과 → 재시도 ({attempt + 1}/2)")
                continue
            raise RuntimeError(f"Gemini 응답 시간 초과: {e}") from e

    cands = (data or {}).get("candidates") or []
    if not cands:
        raise RuntimeError(f"답변이 생성되지 않았습니다: {json.dumps(data)[:300]}")
    parts = cands[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise RuntimeError(f"빈 답변 (finishReason={cands[0].get('finishReason')})")
    return text.strip()


CITE_RE = re.compile(r"\[(\d+)\]")


def answer(
    question: str,
    lat: float | None = None,
    lon: float | None = None,
    k: int = 5,
    location_weight: float = 0.1,
) -> dict:
    """질문 → 검색 → 답변 생성. 답변 본문, 인용된 출처, 지도에 표시할 장소를 반환."""
    segment = (
        R.nearest_segment(lat, lon) if lat is not None and lon is not None else None
    )
    chunks = R.search(question, lat, lon, k, location_weight)

    if not chunks:
        return {
            "answer": "제가 가진 자료에서 관련 내용을 찾지 못했어요.",
            "sources": [],
            "places": [],
            "segment": segment,
        }

    user = f"{build_context(chunks, segment)}\n[질문]\n{question}"
    text = call_gemini(SYSTEM_PROMPT, user)

    # 본문에 처음 나온 순서대로 출처 번호를 1, 2, 3…으로 다시 매김 (없는 번호는 제거)
    cited = []
    for n in CITE_RE.findall(text):
        i = int(n)
        if 1 <= i <= len(chunks) and i not in cited:
            cited.append(i)
    remap = {old: new for new, old in enumerate(cited, 1)}
    text = CITE_RE.sub(
        lambda m: f"[{remap[int(m.group(1))]}]" if int(m.group(1)) in remap else "",
        text,
    )

    sources = [
        {
            "n": remap[i],
            "title": chunks[i - 1]["title"],
            "source": chunks[i - 1]["source"],
            "license": chunks[i - 1]["license"],
            "narrative": chunks[i - 1]["style"] == "narrative",
            "audio_url": chunks[i - 1]["audio_url"],
            "image_url": chunks[i - 1]["image_url"],
            "place_id": chunks[i - 1]["place_id"],
            "excerpt": chunks[i - 1]["content"][:200],
        }
        for i in cited
    ]

    places = []
    for s in sources:
        if s["place_id"] and s["place_id"] not in places:
            places.append(s["place_id"])

    return {
        "answer": text,
        "sources": sources,
        "places": places,
        "segment": segment,
        "model": model_name(),
    }


def list_models() -> list[str]:
    """이 API 키로 답변 생성(generateContent)에 쓸 수 있는 모델 목록"""
    req = urllib.request.Request(
        f"{API}/models?pageSize=200",
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        data = json.load(res)
    return sorted(
        m["name"].removeprefix("models/")
        for m in data.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    )
