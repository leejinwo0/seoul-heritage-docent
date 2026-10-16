"""
02_build_index.py
data/processed 결과를 청킹·임베딩해서 Supabase(PostGIS + pgvector)에 적재합니다.

준비  1) Supabase SQL Editor에서 db/schema.sql 실행
      2) .env 에 DATABASE_URL, GEMINI_API_KEY 설정 (OpenAI를 쓰면 OPENAI_API_KEY)
      3) pip install "psycopg[binary]" python-dotenv pandas   (OpenAI 사용 시 openai 추가)

실행  python scripts/02_build_index.py --dry-run   # DB·API 없이 청킹 결과만 확인
      python scripts/02_build_index.py             # 실제 적재 (변경된 청크만 임베딩)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"

# 임베딩 설정 — 차원(EMBED_DIM)은 db/schema.sql의 vector(1536)과 같아야 함
EMBED_PROVIDER = "gemini"                 # "gemini"(무료 등급) 또는 "openai"
EMBED_MODEL = "gemini-embedding-001" if EMBED_PROVIDER == "gemini" else "text-embedding-3-small"
EMBED_DIM = 1536
EMBED_BATCH = 20        # 무료 등급 분당 토큰 제한을 넘지 않도록 작게
EMBED_SLEEP = 15        # 배치 사이 대기(초). 429 오류가 잦으면 늘리기
MAX_CHARS = 500        # 청크 최대 길이 (한국어 기준 대략 2~4문장)
MIN_CHARS = 120        # 이보다 짧은 마지막 조각은 앞 청크에 붙임


# ---------------------------------------------------------------------------
# 1. 청킹
# ---------------------------------------------------------------------------

SENT_RE = re.compile(r"(?<=[.!?。])\s+|\n+")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_RE.split(text) if s and s.strip()]


def chunk_text(text: str) -> list[str]:
    """문장 경계로 MAX_CHARS 이내로 묶고, 앞 청크의 마지막 문장을 겹쳐 문맥 유지"""
    if len(text) <= MAX_CHARS:
        return [text]
    sents = split_sentences(text)
    chunks, cur = [], []
    for s in sents:
        if cur and len(" ".join(cur + [s])) > MAX_CHARS:
            chunks.append(" ".join(cur))
            cur = [cur[-1]]                      # 1문장 오버랩
        cur.append(s)
    if cur:
        tail = " ".join(cur)
        if chunks and len(tail) < MIN_CHARS:
            chunks[-1] = chunks[-1] + " " + " ".join(cur[1:])
        else:
            chunks.append(tail)
    return chunks


def build_chunks() -> list[dict]:
    rows = []
    with open(PROCESSED / "docs.jsonl", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            for i, part in enumerate(chunk_text(d["text"])):
                # 임베딩에는 제목을 앞에 붙여 "어디에 대한 글인지"를 함께 표현
                embed_input = f"[{d['title']}] {part}"
                rows.append({
                    "chunk_id": f"{d['doc_id']}-{i}",
                    "doc_id": d["doc_id"],
                    "place_id": d.get("place_id"),
                    "segment_ids": d.get("segment_ids") or [],
                    "title": d["title"],
                    "content": part,
                    "source": d.get("source"),
                    "license": d.get("license"),
                    "style": d.get("style"),
                    "audio_url": d.get("audio_url"),
                    "image_url": d.get("image_url"),
                    "embed_input": embed_input,
                    "content_hash": hashlib.sha256(
                        f"{EMBED_MODEL}|{embed_input}".encode()).hexdigest()[:16],
                })
    return rows


# ---------------------------------------------------------------------------
# 2. 임베딩
# ---------------------------------------------------------------------------

def embed(texts: list[str]) -> list[list[float]]:
    return embed_gemini(texts) if EMBED_PROVIDER == "gemini" else embed_openai(texts)


def embed_gemini(texts: list[str]) -> list[list[float]]:
    """Gemini batchEmbedContents REST 호출 (추가 패키지 불필요). 429(한도 초과)는 기다렸다 재시도."""
    key = os.environ["GEMINI_API_KEY"]
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{EMBED_MODEL}:batchEmbedContents")
    out = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i:i + EMBED_BATCH]
        body = json.dumps({"requests": [{
            "model": f"models/{EMBED_MODEL}",
            "content": {"parts": [{"text": t}]},
            "taskType": "RETRIEVAL_DOCUMENT",        # 질문 쪽은 RETRIEVAL_QUERY로 임베딩
            "outputDimensionality": EMBED_DIM,
        } for t in batch]}).encode()
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json", "x-goog-api-key": key})

        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=60) as res:
                    data = json.load(res)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 4:
                    print("  사용량 한도 도달 → 60초 대기 후 재시도")
                    time.sleep(60)
                    continue
                raise RuntimeError(f"Gemini API 오류 {e.code}: {e.read().decode()[:300]}") from e

        vecs = [e["values"] for e in data["embeddings"]]
        if any(len(v) != EMBED_DIM for v in vecs):
            raise RuntimeError(f"임베딩 차원이 {EMBED_DIM}이 아닙니다: {len(vecs[0])}")
        out.extend(vecs)
        print(f"  임베딩 {len(out)}/{len(texts)}")
        if i + EMBED_BATCH < len(texts):
            time.sleep(EMBED_SLEEP)
    return out


def embed_openai(texts: list[str]) -> list[list[float]]:
    from openai import OpenAI
    client = OpenAI()   # OPENAI_API_KEY 환경변수 사용
    out = []
    for i in range(0, len(texts), 64):
        res = client.embeddings.create(model=EMBED_MODEL, input=texts[i:i + 64])
        out.extend(e.embedding for e in res.data)
        print(f"  임베딩 {len(out)}/{len(texts)}")
    return out


def to_vector(v: list[float]) -> str:
    """pgvector 리터럴 문자열 ('[0.1,0.2,...]') — 별도 어댑터 없이 ::vector로 캐스팅"""
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


# ---------------------------------------------------------------------------
# 3. 적재
# ---------------------------------------------------------------------------

def split_list(x) -> list[str]:
    return [s for s in str(x).split("|") if s] if pd.notna(x) else []


def load(chunks: list[dict]):
    import psycopg
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    url = os.environ["DATABASE_URL"]

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        # (1) 구간
        fc = json.load(open(PROCESSED / "segments.geojson", encoding="utf-8"))
        for feat in fc["features"]:
            p = feat["properties"]
            cur.execute("""
                insert into segments (segment_id, name, course, duration, official_length,
                                      difficulty, line_length_m, description, geom)
                values (%s,%s,%s,%s,%s,%s,%s,%s, ST_GeomFromGeoJSON(%s)::geography)
                on conflict (segment_id) do update set
                    name=excluded.name, course=excluded.course, duration=excluded.duration,
                    official_length=excluded.official_length, difficulty=excluded.difficulty,
                    line_length_m=excluded.line_length_m, description=excluded.description,
                    geom=excluded.geom
            """, (p["segment_id"], p["name"], p["course"], p["duration"], p["official_length"],
                  p["difficulty"], p["line_length_m"], p["description"],
                  json.dumps(feat["geometry"])))
        print(f"구간 {len(fc['features'])}개 적재")

        # (2) 장소
        places = pd.read_csv(PROCESSED / "places.csv", encoding="utf-8-sig")
        for _, r in places.iterrows():
            has_xy = pd.notna(r["lat"]) and pd.notna(r["lon"])
            cur.execute("""
                insert into places (place_id, name, aliases, types, segment_ids, geom,
                                    coord_source, segment_source, dist_to_wall_m)
                values (%s,%s,%s,%s,%s,
                        case when %s then ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography end,
                        %s,%s,%s)
                on conflict (place_id) do update set
                    name=excluded.name, aliases=excluded.aliases, types=excluded.types,
                    segment_ids=excluded.segment_ids, geom=excluded.geom,
                    coord_source=excluded.coord_source, segment_source=excluded.segment_source,
                    dist_to_wall_m=excluded.dist_to_wall_m
            """, (r["place_id"], r["name"], split_list(r["aliases"]), split_list(r["types"]),
                  split_list(r["segment_ids"]), has_xy,
                  float(r["lon"]) if has_xy else None, float(r["lat"]) if has_xy else None,
                  r["coord_source"] if pd.notna(r["coord_source"]) else None,
                  r["segment_source"] if pd.notna(r["segment_source"]) else None,
                  int(r["dist_to_wall_m"]) if pd.notna(r["dist_to_wall_m"]) else None))
        print(f"장소 {len(places)}곳 적재")

        # (3) 청크: 내용이 바뀐 것만 임베딩 (비용·시간 절약)
        cur.execute("select chunk_id, content_hash from chunks")
        existing = dict(cur.fetchall())
        todo = [c for c in chunks if existing.get(c["chunk_id"]) != c["content_hash"]]
        print(f"청크 {len(chunks)}개 중 신규/변경 {len(todo)}개 임베딩")

        if todo:
            vectors = embed([c["embed_input"] for c in todo])
            for c, v in zip(todo, vectors):
                cur.execute("""
                    insert into chunks (chunk_id, doc_id, place_id, segment_ids, title, content,
                                        source, license, style, audio_url, image_url,
                                        content_hash, embedding)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, %s::vector)
                    on conflict (chunk_id) do update set
                        doc_id=excluded.doc_id, place_id=excluded.place_id,
                        segment_ids=excluded.segment_ids, title=excluded.title,
                        content=excluded.content, source=excluded.source,
                        license=excluded.license, style=excluded.style,
                        audio_url=excluded.audio_url, image_url=excluded.image_url,
                        content_hash=excluded.content_hash, embedding=excluded.embedding
                """, (c["chunk_id"], c["doc_id"], c["place_id"], c["segment_ids"], c["title"],
                      c["content"], c["source"], c["license"], c["style"], c["audio_url"],
                      c["image_url"], c["content_hash"], to_vector(v)))

        # 데이터에서 사라진 청크 정리
        ids = [c["chunk_id"] for c in chunks]
        cur.execute("delete from chunks where not (chunk_id = any(%s))", (ids,))
        if cur.rowcount:
            print(f"사라진 청크 {cur.rowcount}개 삭제")

        conn.commit()
    print("완료")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="DB·API 없이 청킹 결과만 확인")
    args = ap.parse_args()

    chunks = build_chunks()
    lens = pd.Series([len(c["content"]) for c in chunks])
    n_docs = len({c["doc_id"] for c in chunks})
    print(f"문서 {n_docs}건 → 청크 {len(chunks)}개 "
          f"(길이 중앙값 {int(lens.median())}자, 최대 {lens.max()}자, "
          f"총 {sum(len(c['embed_input']) for c in chunks):,}자)")

    if args.dry_run:
        multi = [c for c in chunks if not c["chunk_id"].endswith("-0")]
        if multi:
            doc = multi[0]["doc_id"]
            print(f"\n예시: {doc} 의 청크")
            for c in chunks:
                if c["doc_id"] == doc:
                    print(f"  [{c['chunk_id']}] {len(c['content'])}자 | {c['content'][:70]}…")
        return

    load(chunks)


if __name__ == "__main__":
    main()
