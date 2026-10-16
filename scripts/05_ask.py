"""
05_ask.py
터미널에서 도슨트에게 질문하고 답변과 출처를 확인합니다.

예시
  python scripts/05_ask.py --list-models
  python scripts/05_ask.py "여기 있는 문은 언제 지어졌어?" --lat 37.5601 --lon 126.9752
  python scripts/05_ask.py "광희문을 왜 시구문이라고 불렀어?"
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import generator as G  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--list-models", action="store_true", help="쓸 수 있는 답변 생성 모델 목록")
    a = ap.parse_args()

    if a.list_models:
        for m in G.list_models():
            print(m)
        return
    if not a.question:
        ap.error("질문을 입력하세요")

    res = G.answer(a.question, a.lat, a.lon, a.k)
    if res["segment"]:
        print(f"현재 위치 → {res['segment']['name']} (성곽까지 {res['segment']['distance_m']:.0f}m)")
    print(f"\n{res['answer']}\n")
    print("출처")
    for s in res["sources"]:
        flag = " · 이야기체 해설" if s["narrative"] else ""
        print(f"  [{s['n']}] {s['title']} — {s['source']} ({s['license']}){flag}")
    if not res["sources"]:
        print("  (인용된 출처 없음)")
    print(f"\n모델: {res.get('model', '-')}")


if __name__ == "__main__":
    main()
