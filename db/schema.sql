-- =====================================================================
-- seoul-heritage-docent DB 스키마 (Supabase SQL Editor에서 한 번 실행)
--   PostGIS  : 구간 선·장소 점 + 거리 계산
--   pgvector : 문서 청크 임베딩 + 유사도 검색
-- =====================================================================

create extension if not exists postgis;
create extension if not exists vector;

-- 1) 성곽 구간 (6개)
create table if not exists segments (
    segment_id      text primary key,          -- baegak, naksan ...
    name            text not null,
    course          text,
    duration        text,
    official_length text,
    difficulty      int,
    line_length_m   int,
    description     text,
    geom            geography(LineString, 4326) not null
);
create index if not exists segments_geom_idx on segments using gist (geom);

-- 2) 장소 기준표 (좌표는 일부만 있음 → nullable)
create table if not exists places (
    place_id        text primary key,           -- P001 ...
    name            text not null,
    aliases         text[] default '{}',
    types           text[] default '{}',
    segment_ids     text[] default '{}',
    geom            geography(Point, 4326),
    coord_source    text,
    segment_source  text,
    dist_to_wall_m  int
);
create index if not exists places_geom_idx on places using gist (geom);
create index if not exists places_segment_idx on places using gin (segment_ids);

-- 3) RAG 청크 (임베딩 차원은 모델에 맞춤: text-embedding-3-small = 1536)
create table if not exists chunks (
    chunk_id        text primary key,           -- D0001-0
    doc_id          text not null,
    place_id        text references places(place_id) on delete set null,
    segment_ids     text[] default '{}',
    title           text,
    content         text not null,
    source          text,
    license         text,
    style           text,                       -- narrative = 극화 서술 포함
    audio_url       text,
    image_url       text,
    content_hash    text not null,              -- 내용이 같으면 재임베딩 생략
    embedding       vector(1536)
);
create index if not exists chunks_embedding_idx
    on chunks using hnsw (embedding vector_cosine_ops);
create index if not exists chunks_segment_idx on chunks using gin (segment_ids);
create index if not exists chunks_place_idx on chunks (place_id);
