-- =====================================================================
-- Nexo · Capa 1 (RAG) · Bloque 0
-- Migración 0001 — Base de conocimiento recuperable (KB-Negocio)
-- Motor: Supabase Postgres + pgvector
-- Multi-tenant desde el día 1 (org_id + RLS). Embeddings: nomic-embed-text (768-dim).
-- Ejecutar en: Supabase → SQL Editor (proyecto qppfampapbxdgednkofc)
-- =====================================================================

-- 1) Extensión de vectores -------------------------------------------------
create extension if not exists vector;

-- Para gen_random_uuid() (Supabase suele traerla; se asegura por si acaso)
create extension if not exists pgcrypto;

-- 2) Tabla de chunks -------------------------------------------------------
create table if not exists public.kb_chunks (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid        not null,                       -- tenant: Kawiil, Yoltik, futuros clientes
  space_id     text        not null default 'general',     -- espacio lógico dentro del tenant
  source_type  text        not null,                       -- 'journal'|'project'|'person'|'doc'|'agent_learning'
  source_ref   text,                                       -- archivo/URL/id de origen (para citar)
  title        text,
  content      text        not null,                       -- el chunk en texto plano
  embedding    vector(768),                                -- nomic-embed-text; nullable hasta que Bloque 1 lo llene
  metadata     jsonb       not null default '{}'::jsonb,    -- fechas, tags, agente autor, etc.
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  constraint kb_chunks_source_type_chk
    check (source_type in ('journal','project','person','doc','agent_learning'))
);

comment on table  public.kb_chunks             is 'Nexo Capa 1 — KB-Negocio recuperable por similitud. NUNCA datos PLD/legal/salud (esos van a KB-Sensible local).';
comment on column public.kb_chunks.org_id      is 'Aislamiento multi-tenant. Sembrar con los UUID reales de Kawiil/Yoltik.';
comment on column public.kb_chunks.embedding   is '768-dim (nomic-embed-text). Nullable hasta backfill/captura del Bloque 1-3.';
comment on column public.kb_chunks.source_ref  is 'Referencia de origen para citar en las respuestas de Nexo.';

-- 3) Índices ---------------------------------------------------------------
-- ANN por similitud coseno (HNSW: buena recuperación con datasets chicos/medianos)
create index if not exists kb_chunks_embedding_hnsw
  on public.kb_chunks using hnsw (embedding vector_cosine_ops);

-- Filtro multi-tenant eficiente
create index if not exists kb_chunks_tenant_idx
  on public.kb_chunks (org_id, space_id, source_type);

-- Búsqueda por metadata (tags, fechas)
create index if not exists kb_chunks_metadata_gin
  on public.kb_chunks using gin (metadata);

-- 4) Trigger updated_at ----------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists trg_kb_chunks_updated_at on public.kb_chunks;
create trigger trg_kb_chunks_updated_at
  before update on public.kb_chunks
  for each row execute function public.set_updated_at();

-- 5) Función de recuperación (top-k por similitud, con filtro de tenant) ----
create or replace function public.match_kb_chunks(
  query_embedding vector(768),
  p_org_id        uuid,
  p_space_id      text default 'general',
  match_count     int  default 8
)
returns table (
  id          uuid,
  source_type text,
  source_ref  text,
  title       text,
  content     text,
  metadata    jsonb,
  similarity  float
)
language sql
stable
as $$
  select
    c.id,
    c.source_type,
    c.source_ref,
    c.title,
    c.content,
    c.metadata,
    1 - (c.embedding <=> query_embedding) as similarity   -- coseno: 1.0 = idéntico
  from public.kb_chunks c
  where c.org_id = p_org_id
    and c.space_id = p_space_id
    and c.embedding is not null
  order by c.embedding <=> query_embedding
  limit match_count;
$$;

comment on function public.match_kb_chunks is 'Nexo — recuperación top-k por coseno filtrada por tenant/espacio. Devuelve similarity en [0,1].';

-- 6) RLS: aislamiento por tenant ------------------------------------------
-- Nota: kawiil-agents accede con SERVICE_KEY (rol service_role), que BYPASSEA RLS.
-- Habilitamos RLS igual para proteger si algún día se accede con anon/authenticated,
-- y para dejar lista la historia multi-tenant del producto.
alter table public.kb_chunks enable row level security;

-- Política para clientes autenticados: solo ven su propio org (claim 'org_id' en el JWT).
drop policy if exists kb_chunks_tenant_isolation on public.kb_chunks;
create policy kb_chunks_tenant_isolation
  on public.kb_chunks
  for all
  to authenticated
  using      (org_id = (auth.jwt() ->> 'org_id')::uuid)
  with check (org_id = (auth.jwt() ->> 'org_id')::uuid);

-- =====================================================================
-- FIN 0001. Checkpoint en checkpoint_0_smoke.sql
-- =====================================================================
