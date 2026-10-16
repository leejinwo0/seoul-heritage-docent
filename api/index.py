"""
api/index.py
웹 화면이 호출하는 API (FastAPI). 로컬과 Vercel 서버리스에서 같은 코드로 동작합니다.

로컬 실행  uvicorn api.index:app --reload
          → http://127.0.0.1:8000/docs 에서 바로 테스트 가능

엔드포인트
  GET  /api/health                 서버·DB 상태
  POST /api/ask                    질문 + 위치 → 답변, 출처, 지도에 표시할 장소
  GET  /api/nearest?lat=&lon=      현재 위치에서 가장 가까운 성곽 구간
  GET  /api/segments               성곽 구간 선 (GeoJSON)
  GET  /api/places                 좌표가 있는 장소 (GeoJSON)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fastapi.staticfiles import StaticFiles

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import generator as G  # noqa: E402
from app import retrieval as R  # noqa: E402

app = FastAPI(title="Seoul Heritage Docent API", version="0.1.0")

# 로컬 개발 시 다른 포트의 화면에서 호출할 수 있도록 허용 (배포 후엔 같은 도메인이라 불필요)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get(
        "CORS_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500"
    ).split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# 서울 대략 범위: 범위 밖 좌표는 위치 없이 처리 (잘못된 값이 위치 점수를 왜곡하지 않도록)
SEOUL = {"lat": (37.40, 37.72), "lon": (126.75, 127.20)}


def in_seoul(lat: float | None, lon: float | None) -> bool:
    return (
        lat is not None
        and lon is not None
        and SEOUL["lat"][0] <= lat <= SEOUL["lat"][1]
        and SEOUL["lon"][0] <= lon <= SEOUL["lon"][1]
    )


class AskRequest(BaseModel):
    question: str = Field(
        ..., min_length=1, max_length=300, examples=["여기 있는 문은 언제 지어졌어?"]
    )
    lat: float | None = Field(None, examples=[37.5601])
    lon: float | None = Field(None, examples=[126.9752])


@app.get("/api/health")
def health():
    try:
        with R.connect() as conn:
            n = conn.execute("select count(*) from chunks").fetchone()[0]
        return {"status": "ok", "chunks": n, "model": G.model_name()}
    except Exception as e:  # DB 연결 문제를 화면에서 바로 알 수 있게
        raise HTTPException(503, f"DB 연결 실패: {type(e).__name__}") from e


@app.post("/api/ask")
def ask(req: AskRequest):
    q = req.question.strip()
    if not q:
        raise HTTPException(400, "질문을 입력해주세요.")
    located = in_seoul(req.lat, req.lon)
    try:
        res = G.answer(q, req.lat if located else None, req.lon if located else None)
    except RuntimeError as e:
        # Gemini 한도 초과·일시 장애 등 → 사용자에게는 짧은 안내만
        print(f"[ask] {e}")
        raise HTTPException(
            503, "지금은 답변을 만들 수 없어요. 잠시 후 다시 시도해주세요."
        ) from e
    res["location_used"] = located
    return res


@app.get("/api/nearest")
def nearest(lat: float = Query(...), lon: float = Query(...)):
    if not in_seoul(lat, lon):
        raise HTTPException(400, "서울 범위 밖의 좌표입니다.")
    return R.nearest_segment(lat, lon)


@app.get("/api/segments")
def segments():
    with R.connect() as conn:
        rows = conn.execute("""
            select segment_id, name, course, duration, official_length, difficulty,
                   ST_AsGeoJSON(geom::geometry, 6)
            from segments order by segment_id
        """).fetchall()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": json.loads(r[6]),
                "properties": {
                    "segment_id": r[0],
                    "name": r[1],
                    "course": r[2],
                    "duration": r[3],
                    "official_length": r[4],
                    "difficulty": r[5],
                },
            }
            for r in rows
        ],
    }


@app.get("/api/places")
def places():
    with R.connect() as conn:
        rows = conn.execute("""
            select place_id, name, types, segment_ids, ST_AsGeoJSON(geom::geometry, 6)
            from places where geom is not null order by place_id
        """).fetchall()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": json.loads(r[4]),
                "properties": {
                    "place_id": r[0],
                    "name": r[1],
                    "types": r[2],
                    "segment_ids": r[3],
                },
            }
            for r in rows
        ],
    }


@app.get("/api/config")
def config():
    # 스마트서울맵 키는 타일 주소에 포함되어 브라우저에 노출되는 값 → 저장소 대신 환경변수로만 관리
    return {"seoul_map_key": os.environ.get("SEOUL_MAP_API_KEY", "")}


# 로컬 개발용: web/ 정적 화면 제공 (API 경로 등록 뒤에 마운트해야 /api/* 가 가려지지 않음)
WEB_DIR = Path(__file__).resolve().parents[1] / "web"
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
