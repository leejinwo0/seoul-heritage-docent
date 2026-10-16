"""
04_evaluate.py
평가 세트(data/eval/questions.csv)로 위치 가중치별 검색 정확도를 비교합니다.

정답 판정  gold 컬럼의 값과 검색된 청크를 비교
           P070       → 청크의 place_id가 P070이면 정답
           SEG:namsan → 장소 없이 남산구간 설명 문서(순성안내 구간정보)이면 정답
           여러 개면 'P070|P071'처럼 | 로 구분 (하나라도 맞으면 정답)
지표      Hit@1  : 1위가 정답인 비율
           Hit@5  : 상위 5개 안에 정답이 있는 비율
           MRR@10 : 정답 순위의 역수 평균 (1위=1.0, 2위=0.5 … 10위 밖=0)

실행  python scripts/04_evaluate.py
      python scripts/04_evaluate.py --weights 0 0.1 0.2 0.3 0.5
결과  data/eval/results_detail.csv  문항·가중치별 정답 순위와 1위 문서
      data/eval/results_summary.csv 가중치 × 유형별 지표
      docs/eval_results.md          README에 붙일 수 있는 표
"""

import argparse
import os
import hashlib
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import retrieval as R  # noqa: E402

EVAL = ROOT / "data" / "eval"
CACHE = EVAL / "query_embeddings.json"   # 질문 임베딩 캐시 (API 호출 절약)
K = 10
TYPE_LABEL = {"deictic": "현장형", "named": "명시형", "segment": "구간형", "all": "전체"}


def load_cache() -> dict:
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}


def embed_all(questions: list[str]) -> dict[str, list[float]]:
    cache = load_cache()
    out = {}
    for q in questions:
        key = hashlib.sha256(f"{R.EMBED_MODEL}|{R.EMBED_DIM}|{q}".encode()).hexdigest()[:16]
        if key not in cache:
            cache[key] = R.embed_query(q)
            time.sleep(0.7)                     # 무료 등급 분당 요청 제한 여유
        out[q] = cache[key]
    CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return out


def is_hit(row: dict, gold: list[str]) -> bool:
    for g in gold:
        if g.startswith("SEG:"):
            seg = g[4:]
            if row["place_id"] is None and row["segment_ids"] == [seg]:
                return True
        elif row["place_id"] == g:
            return True
    return False


def first_hit_rank(results: list[dict], gold: list[str]) -> int | None:
    for i, r in enumerate(results, 1):
        if is_hit(r, gold):
            return i
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", type=float, nargs="+", default=[0.0, 0.1, 0.2, 0.3, 0.5])
    a = ap.parse_args()

    qs = pd.read_csv(EVAL / "questions.csv", encoding="utf-8-sig", dtype={"lat": float, "lon": float})
    print(f"평가 문항 {len(qs)}개, 가중치 {a.weights}")
    vecs = embed_all(qs["question"].tolist())

    import psycopg
    detail = []
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for w in a.weights:
            for _, q in qs.iterrows():
                lat = None if pd.isna(q["lat"]) else float(q["lat"])
                lon = None if pd.isna(q["lon"]) else float(q["lon"])
                res = R.search_by_vector(vecs[q["question"]], lat, lon, K, w, conn)
                gold = str(q["gold"]).split("|")
                rank = first_hit_rank(res, gold)
                detail.append({
                    "weight": w, "qid": q["qid"], "type": q["type"], "question": q["question"],
                    "gold_names": q["gold_names"], "rank": rank,
                    "top1": res[0]["title"] if res else "",
                    "top1_distance_m": round(res[0]["distance_m"]) if res and res[0]["distance_m"] is not None else None,
                })

    df = pd.DataFrame(detail)
    df["hit1"] = df["rank"].eq(1)
    df["hit5"] = df["rank"].le(5).fillna(False)
    df["rr"] = df["rank"].map(lambda r: 0.0 if pd.isna(r) else 1.0 / r)

    parts = [df.assign(type="all"), df]
    summary = (pd.concat(parts)
               .groupby(["weight", "type"])
               .agg(n=("qid", "count"), hit1=("hit1", "mean"), hit5=("hit5", "mean"), mrr=("rr", "mean"))
               .reset_index())

    EVAL.mkdir(parents=True, exist_ok=True)
    df.to_csv(EVAL / "results_detail.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(EVAL / "results_summary.csv", index=False, encoding="utf-8-sig")

    # 화면 출력 + markdown 표
    lines = ["| 위치 가중치 | 유형 | 문항 수 | Hit@1 | Hit@5 | MRR@10 |",
             "|---|---|---|---|---|---|"]
    print()
    for _, r in summary.sort_values(["type", "weight"]).iterrows():
        label = TYPE_LABEL.get(r["type"], r["type"])
        print(f"w={r['weight']:<4} {label:<4} n={r['n']:<3} "
              f"Hit@1 {r['hit1']:.0%}  Hit@5 {r['hit5']:.0%}  MRR {r['mrr']:.3f}")
        lines.append(f"| {r['weight']} | {label} | {r['n']} | {r['hit1']:.0%} | {r['hit5']:.0%} | {r['mrr']:.3f} |")

    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "eval_results.md").write_text(
        "# 검색 평가 결과\n\n" + "\n".join(lines) + "\n", encoding="utf-8")

    # 가중치 0 대비 가장 좋은 가중치에서 달라진 문항 요약
    best_w = summary[summary["type"] == "all"].sort_values("mrr", ascending=False).iloc[0]["weight"]
    base, best = df[df["weight"] == 0.0].set_index("qid"), df[df["weight"] == best_w].set_index("qid")
    if 0.0 in a.weights and best_w != 0.0:
        print(f"\n전체 MRR 기준 최적 가중치: {best_w}")
        changed = [(qid, base.loc[qid, "rank"], best.loc[qid, "rank"]) for qid in base.index
                   if base.loc[qid, "rank"] != best.loc[qid, "rank"]]
        for qid, r0, r1 in changed:
            f = lambda r: "없음" if pd.isna(r) else f"{int(r)}위"
            print(f"  {qid} {base.loc[qid, 'question'][:28]:<28} {f(r0)} → {f(r1)}")
    print(f"\n저장: {EVAL.relative_to(ROOT)}/results_*.csv, docs/eval_results.md")


if __name__ == "__main__":
    main()
