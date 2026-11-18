-- Rol de ESCRITURA ACOTADA para Donna en Patio Yoltik. Correr una vez en el SQL Editor (proyecto udqkldwxforwgncinkrr).
-- Puede: leer, actualizar tareas (sin tocar workspace/proyecto) y agregar comentarios. NO puede borrar nada ni tocar otras tablas.
-- Revocar: drop owned by patio_escritura; drop role patio_escritura;
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'patio_escritura') then
    create role patio_escritura login password 'CAMBIA-OTRA-CONTRASENA-LARGA';
  end if;
end $$;

alter role patio_escritura set statement_timeout = '15s';
grant usage on schema public to patio_escritura;
grant select on public.tasks, public.task_comments, public.projects, public.team_members to patio_escritura;
grant update (titulo, descripcion, estatus, prioridad, asignado_a, fecha_vencimiento, completada_en, updated_at)
  on public.tasks to patio_escritura;
grant insert on public.task_comments to patio_escritura;

do $$
declare t text;
begin
  foreach t in array array['tasks','task_comments','projects','team_members'] loop
    execute format('drop policy if exists patio_escritura_select on public.%I', t);
    execute format('create policy patio_escritura_select on public.%I for select to patio_escritura using (true)', t);
  end loop;
  drop policy if exists patio_escritura_update on public.tasks;
  create policy patio_escritura_update on public.tasks for update to patio_escritura using (true) with check (true);
  drop policy if exists patio_escritura_insert on public.task_comments;
  create policy patio_escritura_insert on public.task_comments for insert to patio_escritura with check (true);
end $$;
