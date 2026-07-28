-- =====================================================================
-- Nexo · Capa 1 · Bloque 0 — CHECKPOINT (smoke test)
-- Objetivo: probar que kb_chunks almacena y que match_kb_chunks ordena por
-- similitud coseno correctamente. Aún NO usa embeddings reales (eso es Bloque 1);
-- usa vectores sintéticos deterministas para que el resultado sea verificable.
--
-- Ejecutar DESPUÉS de 0001_kb_chunks.sql, en el SQL Editor de Supabase.
-- Es idempotente: limpia sus propias filas de prueba al inicio y al final.
-- =====================================================================

-- org de prueba (no es un tenant real)
do $$ begin perform set_config('nexo.test_org', '00000000-0000-0000-0000-000000000001', true); end $$;

-- Limpieza previa (por si se corrió antes)
delete from public.kb_chunks
where org_id = '00000000-0000-0000-0000-000000000001'::uuid;

-- Insertar 3 chunks con vectores sintéticos:
--   A = dim1        -> [1,0,0,...]
--   B = dim2        -> [0,1,0,...]
--   C = dim1 + dim2 -> [1,1,0,...]  (45° respecto a A)
insert into public.kb_chunks (org_id, space_id, source_type, source_ref, title, content, embedding)
values
  ('00000000-0000-0000-0000-000000000001'::uuid, 'general', 'doc',
   'smoke/A', 'Chunk A',
   'Kailash es la plataforma de pagos B2B de Yoltik.',
   ('[1'         || repeat(',0',767) || ']')::vector(768)),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'general', 'doc',
   'smoke/B', 'Chunk B',
   'Ikan es el producto de cumplimiento PLD LFPIORPI.',
   ('[0,1'       || repeat(',0',766) || ']')::vector(768)),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'general', 'doc',
   'smoke/C', 'Chunk C',
   'Yoltik es la marca de productos desarrollados por Kawiil.',
   ('[1,1'       || repeat(',0',766) || ']')::vector(768));

-- Consulta = vector dim1 (idéntico a A).
-- Orden esperado:  A (similarity = 1.0), C (~0.7071), B (0.0)
select source_ref, title, round(similarity::numeric, 4) as similarity
from public.match_kb_chunks(
  ('[1' || repeat(',0',767) || ']')::vector(768),   -- query_embedding
  '00000000-0000-0000-0000-000000000001'::uuid,     -- p_org_id
  'general',                                        -- p_space_id
  8                                                 -- match_count
);

-- RESULTADO ESPERADO (3 filas, en este orden):
--   source_ref | title   | similarity
--   smoke/A    | Chunk A | 1.0000
--   smoke/C    | Chunk C | 0.7071
--   smoke/B    | Chunk B | 0.0000
--
-- Si el orden y los valores coinciden -> Bloque 0 VERDE:
--   * pgvector activo, tabla y tipos correctos,
--   * índice y operador de coseno funcionando,
--   * match_kb_chunks filtra por tenant y ordena bien.

-- Limpieza final
delete from public.kb_chunks
where org_id = '00000000-0000-0000-0000-000000000001'::uuid;
