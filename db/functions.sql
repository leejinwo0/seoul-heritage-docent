-- =====================================================================
-- search_chunks : 질문 임베딩 + 사용자 위치로 청크 검색 (2단계 위치 검색)
--
--  1단계  사용자 위치에서 가까운 성곽 구간을 찾음
--         (반경 segment_radius_m 이내 구간 + 가장 가까운 구간 1개)
--  2단계  후보 청크 = 벡터 유사도 상위 candidate_count개
--                   ∪ 근처 구간에 속한 청크 ∪ 근처 좌표 장소의 청크
--         최종 점수 = (1 - w) * 유사도 + w * 위치점수
--           위치점수 = 장소 좌표가 있으면 실제 거리 점수 exp(-거리/decay_m)
--                      없으면 소속 구간까지 거리로 0.7 * exp(-구간거리/decay_m)
--           (좌표가 구간 소속보다 정확하므로 구간 점수는 0.7배로 낮춤.
--            좌표 있는 장소에 '최근접구간'으로 붙은 구간은 점수 계산에 쓰지 않음)
--
--  location_weight = 0 이면 순수 벡터 검색 → 평가에서 비교 기준선으로 사용
--  위치를 안 넘기면(null) 위치점수 0 → 유사도 순 정렬과 같음
-- =====================================================================

create or replace function search_chunks(
    query_embedding   vector(1536),
    user_lat          double precision default null,
    user_lon          double precision default null,
    match_count       int              default 5,
    location_weight   double precision default 0.1,
    segment_radius_m  double precision default 300,
    decay_m           double precision default 500,
    candidate_count   int              default 50
)
returns table (
    chunk_id        text,
    doc_id          text,
    place_id        text,
    place_name      text,
    segment_ids     text[],
    title           text,
    content         text,
    source          text,
    license         text,
    style           text,
    audio_url       text,
    image_url       text,
    similarity      double precision,
    distance_m      double precision,
    location_score  double precision,
    score           double precision
)
language sql stable
as $$
with
user_pt as (
    select case when user_lat is not null and user_lon is not null
                then ST_SetSRID(ST_MakePoint(user_lon, user_lat), 4326)::geography
           end as g
),
seg_dist as (
    select s.segment_id, ST_Distance(s.geom, u.g) as d
    from segments s cross join user_pt u
    where u.g is not null
),
near_segs as (
    select sd.segment_id, sd.d
    from seg_dist sd
    where sd.d <= segment_radius_m
       or sd.d = (select min(d) from seg_dist)
),
vec_cand as (
    select c.chunk_id
    from chunks c
    order by c.embedding <=> query_embedding
    limit candidate_count
),
loc_cand as (
    select c.chunk_id
    from chunks c
    where c.segment_ids && (select coalesce(array_agg(ns.segment_id), '{}'::text[]) from near_segs ns)
    union
    select c.chunk_id
    from chunks c
    join places p on p.place_id = c.place_id
    cross join user_pt u
    where u.g is not null and p.geom is not null
      and ST_DWithin(p.geom, u.g, decay_m * 3)
),
cand as (
    select vc.chunk_id from vec_cand vc
    union
    select lc.chunk_id from loc_cand lc
),
scored as (
    select
        c.chunk_id, c.doc_id, c.place_id, p.name as place_name, c.segment_ids,
        c.title, c.content, c.source, c.license, c.style, c.audio_url, c.image_url,
        1 - (c.embedding <=> query_embedding) as similarity,
        case when u.g is not null and p.geom is not null
             then ST_Distance(p.geom, u.g) end as distance_m,
        (select min(ns.d) from near_segs ns where ns.segment_id = any(c.segment_ids)) as seg_d
    from cand k
    join chunks c on c.chunk_id = k.chunk_id
    left join places p on p.place_id = c.place_id
    cross join user_pt u
),
final as (
    select s.*,
           case
               when s.distance_m is not null then exp(-s.distance_m / decay_m)   -- 좌표 있음: 실제 거리
               else coalesce(0.7 * exp(-s.seg_d / decay_m), 0)                   -- 좌표 없음: 구간 소속
           end as loc_score
    from scored s
)
select
    f.chunk_id, f.doc_id, f.place_id, f.place_name, f.segment_ids,
    f.title, f.content, f.source, f.license, f.style, f.audio_url, f.image_url,
    f.similarity, f.distance_m, f.loc_score,
    (1 - location_weight) * f.similarity + location_weight * f.loc_score as score
from final f
order by score desc
limit match_count;
$$;

-- 확인용: 위치만으로 가장 가까운 구간 (지도 화면에서 "현재 구간" 표시에 사용)
create or replace function nearest_segment(user_lat double precision, user_lon double precision)
returns table (segment_id text, name text, distance_m double precision)
language sql stable
as $$
    select s.segment_id, s.name,
           ST_Distance(s.geom, ST_SetSRID(ST_MakePoint(user_lon, user_lat), 4326)::geography)
    from segments s
    order by s.geom <-> ST_SetSRID(ST_MakePoint(user_lon, user_lat), 4326)::geography
    limit 1;
$$;
