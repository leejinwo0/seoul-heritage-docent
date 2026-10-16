"""
app/retrieval.py
질문 임베딩 + search_chunks DB 함수 호출. 나중에 FastAPI 백엔드에서도 그대로 import해서 씁니다.

임베딩 모델·차원은 scripts/02_build_index.py와 반드시 같아야 합니다.
(문서는 RETRIEVAL_DOCUMENT, 질문은 RETRIEVAL_QUERY로 임베딩)
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 1536


def embed_query(text: str) -> list[float]:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{EMBED_MODEL}:embedContent")
    body = json.dumps({
        "model": f"models/{EMBED_MODEL}",
        "content": {"parts": [{"text": text}]},
        "taskType": "RETRIEVAL_QUERY",
        "outputDimensionality": EMBED_DIM,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.load(res)["embedding"]["values"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(20)
                continue
            raise RuntimeError(f"Gemini API 오류 {e.code}: {e.read().decode()[:300]}") from e
    raise RuntimeError("임베딩 재시도 실패")


def connect() -> psycopg.Connection:
    """DB 연결. prepare_threshold=None: Supabase 풀러(특히 Transaction 모드)와 충돌하는
    서버 측 prepared statement를 쓰지 않음 → 로컬·Vercel 어디서나 안전"""
    return psycopg.connect(os.environ["DATABASE_URL"], prepare_threshold=None)


def to_vector(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


COLUMNS = ["chunk_id", "doc_id", "place_id", "place_name", "segment_ids", "title", "content",
           "source", "license", "style", "audio_url", "image_url",
           "similarity", "distance_m", "location_score", "score"]


def search_by_vector(vec: list[float], lat: float | None = None, lon: float | None = None,
                     k: int = 5, location_weight: float = 0.1,
                     conn: psycopg.Connection | None = None) -> list[dict]:
    """이미 임베딩된 질문 벡터로 검색 (평가에서 같은 질문을 가중치별로 여러 번 돌릴 때 사용)"""
    own = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute(
            "select * from search_chunks(%s::vector, %s, %s, %s, %s)",
            (to_vector(vec), lat, lon, k, location_weight)).fetchall()
    finally:
        if own:
            conn.close()
    return [dict(zip(COLUMNS, r)) for r in rows]


def search(question: str, lat: float | None = None, lon: float | None = None,
           k: int = 5, location_weight: float = 0.1,
           conn: psycopg.Connection | None = None) -> list[dict]:
    """질문과 위치로 관련 청크 k개 반환 (점수 높은 순)"""
    return search_by_vector(embed_query(question), lat, lon, k, location_weight, conn)


def nearest_segment(lat: float, lon: float, conn: psycopg.Connection | None = None) -> dict | None:
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute("select * from nearest_segment(%s, %s)", (lat, lon)).fetchone()
    finally:
        if own:
            conn.close()
    return dict(zip(["segment_id", "name", "distance_m"], row)) if row else None
