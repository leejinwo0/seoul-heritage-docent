"""
01_build_dataset.py
서울 열린데이터광장 한양도성 공공데이터를 RAG + 지도용 데이터셋으로 정제합니다.

입력  data/raw/*.csv           (열린데이터광장에서 받은 원본 CSV, 파일명 그대로)
      data/manual/place_overrides.csv  (선택: 수동 보정값, 없으면 건너뜀)
출력  data/processed/segments.geojson  구간 선 + 구간 설명
      data/processed/places.csv        장소 기준표 (segment_ids, 좌표, 좌표 출처)
      data/processed/docs.jsonl        RAG용 문서 (place_id / segment_ids로 연결)
      data/processed/disconnects.csv   단절구간 목록 (지도 레이어용)
      data/processed/places_review.csv 구간·좌표가 비어 수동 확인이 필요한 장소

실행  python scripts/01_build_dataset.py
"""

from __future__ import annotations

import html
import json
import math
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
MANUAL = ROOT / "data" / "manual"
OUT = ROOT / "data" / "processed"

# ---------------------------------------------------------------------------
# 1. 설정
# ---------------------------------------------------------------------------

# 파일명 대신 키워드로 찾기 (파일명이 조금 달라도 동작하도록)
FILES = {
    "lines": "순성길_6개구간_정보_국문",
    "seg_info": "순성안내_구간정보",
    "major": "순성안내_구간_주요지점_정보",
    "nearby": "주변유적지_정보_국문",
    "poi": "관심지점정보_국문",
    "audio": "오디오_해설정보_국문",
    "stone": "각자성석_정보_국문",
    "disconn": "단절구간_정보_국문",
}

# 데이터마다 다른 구간 번호 체계를 하나의 segment_id로 통일
#   seq        : 순성길 6개구간 정보의 구간정보SEQ
#   course_no  : 순성안내 구간정보/주요지점의 구간 관리번호(KOR)
#   keyword    : 각자성석 '구간' 컬럼 등 문자열 매칭용
SEGMENTS = [
    {
        "segment_id": "baegak",
        "name": "백악구간",
        "seq": 1,
        "course_no": 1,
        "keyword": "백악",
    },
    {
        "segment_id": "naksan",
        "name": "낙산구간",
        "seq": 2,
        "course_no": 3,
        "keyword": "낙산",
    },
    {
        "segment_id": "heunginjimun",
        "name": "흥인지문구간",
        "seq": 3,
        "course_no": 5,
        "keyword": "흥인지문",
    },
    {
        "segment_id": "namsan",
        "name": "남산(목멱산)구간",
        "seq": 11,
        "course_no": 7,
        "keyword": "남산",
    },
    {
        "segment_id": "sungnyemun",
        "name": "숭례문구간",
        "seq": 12,
        "course_no": 9,
        "keyword": "숭례문",
    },
    {
        "segment_id": "inwangsan",
        "name": "인왕산구간",
        "seq": 13,
        "course_no": 11,
        "keyword": "인왕산",
    },
]
SEQ_TO_SEG = {s["seq"]: s["segment_id"] for s in SEGMENTS}
COURSE_TO_SEG = {s["course_no"]: s["segment_id"] for s in SEGMENTS}

# 이용조건: 열린데이터광장 각 데이터 페이지에서 확인 후 필요하면 수정
LICENSE = {
    "lines": "공공누리 4유형 (서울역사박물관)",
    "seg_info": "공공누리 1유형 (문화유산과)",
    "major": "공공누리 1유형 (문화유산과)",
    "nearby": "공공누리 4유형 (서울역사박물관)",
    "poi": "공공누리 4유형 (서울역사박물관)",
    "audio": "공공누리 4유형 (서울역사박물관)",
    "stone": "공공누리 4유형 (서울역사박물관)",
    "disconn": "공공누리 4유형 (서울역사박물관)",
}

# 같은 장소인데 이름이 다른 경우: 정규화된 이름(공백·점·한자괄호 제거) -> 대표 키
# 실행 후 places.csv의 match_key 컬럼을 보고 계속 추가하면 됩니다.
ALIASES = {
    "구러시아공사관": "러시아공사관",
    "안중근의사기념관": "안중근기념관",
    "목멱산봉수대터": "목멱산봉수대",
    "와룡공원-북정마을": "와룡공원",
    "배재학당동관배재학당역사박물관": "배재학당역사박물관",
    "배제학당역사박물관": "배재학당역사박물관",  # 원본 오타(배제)
    "홍파동홍난파가옥": "홍난파가옥",
    "남산목멱산팔각정": "남산팔각정과국사당터",
    "카톨릭대학뒷길": "가톨릭대학교뒷길",
    "인왕산국사당": "인왕산국사당과선바위",
    "동대문성곽공원각자성석": "동대문성곽공원",
}

# 이름 매칭으로 구간을 못 찾은 장소에 적용하는 키워드 규칙 (위에서부터 먼저 걸리는 것 적용)
# 결과는 segment_source='키워드추정'으로 표시되어 places_review.csv에서 검토할 수 있습니다.
SEGMENT_HINTS = [
    ("청운공원", "inwangsan"),
    ("인왕", "inwangsan"),
    ("국사당", "inwangsan"),
    ("월암", "inwangsan"),
    ("백악", "baegak"),
    ("청운대", "baegak"),
    ("말바위", "baegak"),
    ("숙정문", "baegak"),
    ("암문", "baegak"),
    ("121사태", "baegak"),
    ("낙산", "naksan"),
    ("와룡공원", "naksan"),
    ("가톨릭", "naksan"),
    ("카톨릭", "naksan"),
    ("장수마을", "naksan"),
    ("이화마을", "naksan"),
    ("이간수문", "heunginjimun"),
    ("오간수문", "heunginjimun"),
    ("동대문", "heunginjimun"),
    ("광희문", "heunginjimun"),
    ("한양도성유적전시관", "heunginjimun"),
    ("장충", "heunginjimun"),
    ("잠두봉", "namsan"),
    ("목멱", "namsan"),
    ("남산", "namsan"),
    ("백범광장", "namsan"),
    ("나무계단", "namsan"),
    ("성곽마루", "namsan"),
    ("숭례문", "sungnyemun"),
    ("소의문", "sungnyemun"),
    ("정동", "sungnyemun"),
    ("이화학당", "sungnyemun"),
]

# MVP에서 제외할 관심지점 타입 (편의시설 등)
EXCLUDE_POI_TYPES = {"CONVENIENCE"}


# ---------------------------------------------------------------------------
# 2. 공통 유틸
# ---------------------------------------------------------------------------


def _simplify(s: str) -> str:
    """공백·밑줄·괄호를 지워 파일명 형식 차이를 없앰"""
    return re.sub(r"[\s_()（）]", "", s)


def find_file(keyword: str) -> Path:
    key = _simplify(keyword)
    hits = sorted(p for p in RAW.glob("*.csv") if key in _simplify(p.stem))
    if not hits:
        raise FileNotFoundError(f"data/raw 에서 '{keyword}' 파일을 찾을 수 없습니다.")
    return hits[0]


def read_csv(key: str) -> pd.DataFrame:
    path = find_file(FILES[key])
    for enc in ("cp949", "utf-8-sig", "utf-8"):
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError(f"인코딩을 판별할 수 없습니다: {path}")


TAG_RE = re.compile(r"<[^>]+>")
# 원본에서 가운뎃점(·)이 '?'로 깨진 경우: 1?21, 봄?가을, 대한?서울
BROKEN_DOT_RE = re.compile(r"(?<=[가-힣0-9A-Za-z])\?(?=[가-힣0-9A-Za-z])")


def clean_text(x) -> str:
    if pd.isna(x):
        return ""
    s = str(x)
    s = re.sub(r"<br\s*/?>|</p>|</tr>", "\n", s, flags=re.I)  # 줄바꿈 의미 태그
    s = TAG_RE.sub(" ", s)
    s = html.unescape(html.unescape(s))  # &amp;lt; 같은 이중 인코딩 대비
    s = s.replace("\xa0", " ")
    s = BROKEN_DOT_RE.sub("·", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()


def norm_name(name: str) -> str:
    """매칭용 키: 괄호(한자) 제거, 공백·말줄임표 제거, 별칭 적용"""
    s = clean_text(name)
    s = re.sub(
        r"[\(（][^\)）]*[\u4e00-\u9fff][^\)）]*[\)）]", "", s
    )  # 한자 괄호만 제거: (彰義門)
    s = re.sub(r"[\(（\)）]", "", s)  # 나머지 괄호는 기호만 제거
    s = re.sub(r"…|\.{2,}", "", s)
    s = re.sub(r"[\s·.]", "", s)  # 1·21 / 1.21 통일
    return ALIASES.get(s, s)


def display_name(name: str) -> str:
    return clean_text(name)


def valid_coord(lat, lon) -> bool:
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    return 37.4 < lat < 37.7 and 126.8 < lon < 127.2  # 서울 범위 밖·0값 제거


def haversine(lat1, lon1, lat2, lon2) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# ---------------------------------------------------------------------------
# 3. 구간 (segments.geojson)
# ---------------------------------------------------------------------------


def build_segments() -> tuple[dict, dict]:
    lines = read_csv("lines")
    info = read_csv("seg_info")
    info = info[info["어권구분"] == "KOR"]

    geoms = {}  # segment_id -> [(lat, lon), ...]
    for _, row in lines.iterrows():
        seg_id = SEQ_TO_SEG.get(int(row["구간정보SEQ"]))
        if not seg_id:
            continue
        pts = []
        for pair in str(row["구간정보"]).split(","):
            parts = pair.split()
            if len(parts) == 2 and valid_coord(parts[0], parts[1]):
                pts.append(
                    (float(parts[0]), float(parts[1]))
                )  # 이 파일은 '위도 경도' 순서
        geoms[seg_id] = pts

    features = []
    for seg in SEGMENTS:
        sid = seg["segment_id"]
        pts = geoms.get(sid, [])
        length_m = sum(haversine(*pts[i], *pts[i + 1]) for i in range(len(pts) - 1))
        meta = info[info["구간 관리번호"] == seg["course_no"]]
        m = meta.iloc[0] if len(meta) else None
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[lon, lat] for lat, lon in pts],
                },  # GeoJSON은 [경도, 위도]
                "properties": {
                    "segment_id": sid,
                    "name": seg["name"],
                    "course": clean_text(m["구간 코스요약"]) if m is not None else "",
                    "duration": clean_text(m["소요 시간"]) if m is not None else "",
                    "official_length": clean_text(m["구간 길이"])
                    if m is not None
                    else "",
                    "difficulty": None
                    if m is None or pd.isna(m["난이도"])
                    else int(m["난이도"]),
                    "line_length_m": round(length_m),
                    "description": clean_text(m["구간 정보"]) if m is not None else "",
                    "source": "서울시 한양도성 순성길 6개구간 정보(국문) / 순성안내 구간정보",
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}, geoms


def nearest_segment(lat: float, lon: float, geoms: dict) -> tuple[str, float]:
    """가장 가까운 구간과 성곽선까지 거리(m). 꼭짓점 기준 근사(점 간격이 촘촘해 충분)."""
    best = ("", float("inf"))
    for sid, pts in geoms.items():
        d = min(haversine(lat, lon, p[0], p[1]) for p in pts)
        if d < best[1]:
            best = (sid, d)
    return best


# ---------------------------------------------------------------------------
# 4. 장소 + 문서 (places.csv, docs.jsonl)
# ---------------------------------------------------------------------------


class Registry:
    """정규화된 이름 기준으로 장소를 합치고, 문서를 장소에 연결"""

    def __init__(self):
        self.places: dict[str, dict] = {}
        self.docs: list[dict] = []
        self._seen_text: set[tuple[str, int]] = set()

    def place(self, raw_name: str, ptype: str, source: str) -> dict:
        key = norm_name(raw_name)
        p = self.places.get(key)
        if p is None:
            p = {
                "key": key,
                "name": display_name(raw_name),
                "aliases": set(),
                "types": set(),
                "segment_ids": set(),
                "lat": None,
                "lon": None,
                "coord_source": "",
                "segment_source": "",
                "sources": set(),
            }
            self.places[key] = p
        p["aliases"].add(display_name(raw_name))
        p["types"].add(ptype)
        p["sources"].add(source)
        return p

    @staticmethod
    def set_coord(p: dict, lat, lon, source: str):
        if p["lat"] is None and valid_coord(lat, lon):
            p["lat"], p["lon"] = round(float(lat), 7), round(float(lon), 7)
            p["coord_source"] = source

    def add_doc(
        self,
        p: dict | None,
        title: str,
        text: str,
        source_key: str,
        source_name: str,
        segment_ids=None,
        extra: dict | None = None,
    ):
        text = clean_text(text)
        if not text:
            return
        key = (p["key"] if p else "", hash(text))
        if key in self._seen_text:  # 같은 장소·같은 본문 중복 제거
            return
        self._seen_text.add(key)
        self.docs.append(
            {
                "place_key": p["key"] if p else None,
                "segment_ids": sorted(segment_ids or []),
                "title": display_name(title),
                "text": text,
                "source": source_name,
                "license": LICENSE[source_key],
                **(extra or {}),
            }
        )


def build_places_and_docs(geoms: dict) -> Registry:
    reg = Registry()

    # (1) 주요지점: 구간 번호가 있음 (좌표 없음)
    major = read_csv("major")
    for _, r in major.iterrows():
        p = reg.place(r["지점 명"], "MAJOR", "주요지점")
        sid = COURSE_TO_SEG.get(int(r["구간 관리번호"]))
        if sid:
            p["segment_ids"].add(sid)
            p["segment_source"] = "주요지점"
        reg.add_doc(
            p,
            r["지점 명"],
            r["지점 설명"],
            "major",
            "순성안내 구간 주요지점 정보",
            extra={"image_url": clean_text(r["지점 이미지 저장경로"])},
        )

    # (2) 주변유적지: 좌표 있음
    nearby = read_csv("nearby")
    for _, r in nearby.iterrows():
        p = reg.place(r["제목"], "NEARBY", "주변유적지")
        reg.set_coord(p, r["위도"], r["경도"], "주변유적지")
        reg.add_doc(p, r["제목"], r["소개"], "nearby", "한양도성 주변유적지 정보")

    # (3) 각자성석: 구간 문자열 + 일부 좌표
    stone = read_csv("stone")
    for _, r in stone.iterrows():
        p = reg.place(r["제목"], "STONE", "각자성석")
        for seg in SEGMENTS:
            if seg["keyword"] in str(r["구간"]):
                p["segment_ids"].add(seg["segment_id"])
                p["segment_source"] = p["segment_source"] or "각자성석"
        reg.set_coord(p, r["위도"], r["경도"], "각자성석")
        reg.add_doc(p, r["제목"], r["소개"], "stone", "한양도성 각자성석 정보")

    # (4) 관심지점: 텍스트만
    poi = read_csv("poi")
    for _, r in poi.iterrows():
        if r["타입"] in EXCLUDE_POI_TYPES:
            continue
        p = reg.place(r["제목"], r["타입"], "관심지점")
        reg.add_doc(p, r["제목"], r["소개"], "poi", "한양도성 관심지점정보")

    # (5) 오디오 해설: 긴 서사 텍스트 + mp3
    audio = read_csv("audio")
    for _, r in audio.iterrows():
        p = reg.place(r["제목"], "AUDIO", "오디오해설")
        reg.add_doc(
            p,
            r["제목"],
            r["소개"],
            "audio",
            "한양도성 오디오 해설정보",
            extra={
                "caption": clean_text(r["캡션"]),
                "audio_url": clean_text(r["오디오"]),
                "style": "narrative",
            },
        )  # 극화된 서술이 섞여 있음 → 생성 시 단정 금지

    # (6) 수동 보정 적용 (data/manual/place_overrides.csv)
    overrides = MANUAL / "place_overrides.csv"
    if overrides.exists():
        ov = pd.read_csv(overrides, encoding="utf-8-sig")
        for _, r in ov.iterrows():
            p = reg.places.get(norm_name(r["name"]))
            if p is None:
                print(f"  [override] 장소를 찾지 못함: {r['name']}")
                continue
            if isinstance(r.get("segment_ids"), str) and r["segment_ids"].strip():
                p["segment_ids"] = {s.strip() for s in r["segment_ids"].split("|")}
                p["segment_source"] = "수동"
            if valid_coord(r.get("lat"), r.get("lon")):
                p["lat"], p["lon"] = float(r["lat"]), float(r["lon"])
                p["coord_source"] = "수동"

    # (7) 좌표가 있는 장소: 가장 가까운 구간과 성곽까지 거리 계산
    for p in reg.places.values():
        p["dist_to_wall_m"] = None
        if p["lat"] is not None:
            sid, d = nearest_segment(p["lat"], p["lon"], geoms)
            p["dist_to_wall_m"] = round(d)
            if not p["segment_ids"]:
                p["segment_ids"].add(sid)
                p["segment_source"] = "최근접구간"

    # (8) 그래도 구간이 없으면 키워드 규칙으로 추정
    #     이름 → 문서의 '위치 : …' 문구 순서로 보고, 가장 앞에 나오는 키워드를 채택
    #     (해설 본문 전체를 보면 다른 구간 이름이 섞여 오판하므로 위치 문구만 사용)
    loc_text = {}
    for d in reg.docs:
        if d["place_key"] and d["place_key"] not in loc_text:
            m = re.search(r"위치\s*:\s*(.*?)각자일련번호", d["text"])
            loc_text[d["place_key"]] = m.group(1) if m else d["text"][:40]

    def first_hint(target: str):
        target = re.sub(r"[\s·.]", "", target)
        found = [
            (target.find(kw), i, seg)
            for i, (kw, seg) in enumerate(SEGMENT_HINTS)
            if kw in target
        ]
        return min(found)[2] if found else None

    for p in reg.places.values():
        if p["segment_ids"]:
            continue
        hit = first_hint(p["key"]) or first_hint(loc_text.get(p["key"], ""))
        if hit:
            p["segment_ids"].add(hit)
            p["segment_source"] = "키워드추정"

    return reg


# ---------------------------------------------------------------------------
# 5. 저장
# ---------------------------------------------------------------------------


def save(segments_fc: dict, reg: Registry):
    OUT.mkdir(parents=True, exist_ok=True)

    with open(OUT / "segments.geojson", "w", encoding="utf-8") as f:
        json.dump(segments_fc, f, ensure_ascii=False, indent=1)

    # place_id는 이름순으로 고정 부여 (재실행해도 같은 데이터면 같은 ID)
    ordered = sorted(reg.places.values(), key=lambda p: p["key"])
    key_to_id = {p["key"]: f"P{i:03d}" for i, p in enumerate(ordered, 1)}

    rows = []
    for p in ordered:
        rows.append(
            {
                "place_id": key_to_id[p["key"]],
                "name": p["name"],
                "aliases": "|".join(sorted(p["aliases"])),
                "types": "|".join(sorted(p["types"])),
                "segment_ids": "|".join(sorted(p["segment_ids"])),
                "lat": p["lat"],
                "lon": p["lon"],
                "coord_source": p["coord_source"],
                "segment_source": p["segment_source"],
                "dist_to_wall_m": p["dist_to_wall_m"],
                "sources": "|".join(sorted(p["sources"])),
                "match_key": p["key"],
            }
        )
    places = pd.DataFrame(rows)
    places.to_csv(OUT / "places.csv", index=False, encoding="utf-8-sig")

    # 검토 대상: 구간이 없거나, 키워드로 추정한 것 (좌표는 2단계 검색 구조라 필수 아님)
    review = places[
        (places["segment_ids"] == "") | (places["segment_source"] == "키워드추정")
    ]
    review.to_csv(OUT / "places_review.csv", index=False, encoding="utf-8-sig")

    # 장소에 연결된 문서의 segment_ids는 장소 정보로 채움
    place_seg = {p["key"]: sorted(p["segment_ids"]) for p in ordered}
    with open(OUT / "docs.jsonl", "w", encoding="utf-8") as f:
        n = 0
        for d in reg.docs:
            n += 1
            pk = d.pop("place_key")
            d = {
                "doc_id": f"D{n:04d}",
                "place_id": key_to_id.get(pk) if pk else None,
                **d,
            }
            if pk and not d["segment_ids"]:
                d["segment_ids"] = place_seg[pk]
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

        # 구간 설명도 문서로 추가 (place_id 없음)
        for feat in segments_fc["features"]:
            pr = feat["properties"]
            if not pr["description"]:
                continue
            n += 1
            f.write(
                json.dumps(
                    {
                        "doc_id": f"D{n:04d}",
                        "place_id": None,
                        "segment_ids": [pr["segment_id"]],
                        "title": pr["name"],
                        "text": pr["description"],
                        "source": "순성안내 구간정보",
                        "license": LICENSE["seg_info"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    disc = read_csv("disconn")
    pd.DataFrame(
        {
            "disconnect_id": disc["단절구간 번호"],
            "name": disc["제목"].map(clean_text),
            "route_image_url": disc["경로이미지"].map(clean_text),
            "license": LICENSE["disconn"],
        }
    ).to_csv(OUT / "disconnects.csv", index=False, encoding="utf-8-sig")

    return places, review, n


def main():
    print("구간 생성 중...")
    segments_fc, geoms = build_segments()
    for feat in segments_fc["features"]:
        pr = feat["properties"]
        print(
            f"  {pr['name']:<12} 점 {len(feat['geometry']['coordinates']):>4}개, "
            f"선 길이 {pr['line_length_m'] / 1000:.2f}km (공식 {pr['official_length']})"
        )

    print("장소·문서 생성 중...")
    reg = build_places_and_docs(geoms)
    places, review, n_docs = save(segments_fc, reg)

    has_coord = places["lat"].notna().sum()
    has_seg = (places["segment_ids"] != "").sum()
    print(f"\n완료 → {OUT.relative_to(ROOT)}")
    print(f"  장소 {len(places)}곳 (좌표 {has_coord}곳, 구간 연결 {has_seg}곳)")
    print(f"  문서 {n_docs}건")
    print(
        f"  구간 출처: {places['segment_source'].replace('', '(없음)').value_counts().to_dict()}"
    )
    print(f"  검토 필요 {len(review)}곳 → places_review.csv (구간 없음 + 키워드 추정)")


if __name__ == "__main__":
    main()
