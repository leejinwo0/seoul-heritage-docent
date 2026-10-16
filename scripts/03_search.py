"""
03_search.py
터미널에서 검색 결과를 확인하는 도구입니다.

예시
  python scripts/03_search.py "숭례문은 언제 지어졌어?"
  python scripts/03_search.py "여기 성벽은 언제 쌓았어?" --lat 37.5600 --lon 126.9753
  python scripts/03_search.py "여기 성벽은 언제 쌓았어?" --lat 37.5600 --lon 126.9753 --compare
      → 위치 반영(w=0.3)과 순수 벡터 검색(w=0)을 나란히 비교
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.retrieval import nearest_segment, search  # noqa: E402


def show(results: list[dict], label: str):
    print(f"\n== {label}")
    for i, r in enumerate(results, 1):
        dist = f"{r['distance_m']:.0f}m" if r["distance_m"] is not None else "좌표없음"
        print(f"{i}. [{r['title']}] 점수 {r['score']:.3f} "
              f"(유사도 {r['similarity']:.3f}, 위치 {r['location_score']:.3f}, {dist})")
        print(f"   {r['content'][:90].replace(chr(10), ' ')}…")
        print(f"   출처: {r['source']}" + (" · 극화 서술 포함" if r["style"] == "narrative" else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("-w", "--weight", type=float, default=0.1, help="위치 가중치 (0이면 순수 벡터)")
    ap.add_argument("--compare", action="store_true", help="위치 반영 vs 순수 벡터 비교")
    a = ap.parse_args()

    if a.lat is not None and a.lon is not None:
        seg = nearest_segment(a.lat, a.lon)
        if seg:
            print(f"현재 위치 → {seg['name']} (성곽까지 {seg['distance_m']:.0f}m)")

    show(search(a.question, a.lat, a.lon, a.k, a.weight), f"위치 가중치 {a.weight}")
    if a.compare:
        show(search(a.question, a.lat, a.lon, a.k, 0.0), "순수 벡터 검색 (가중치 0)")


if __name__ == "__main__":
    main()
