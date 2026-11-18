-- Rol de SOLO LECTURA para que Donna consulte Patio Yoltik. Correr UNA vez en el SQL Editor de Supabase
-- (proyecto udqkldwxforwgncinkrr). Cambia la contraseña por una larga y aleatoria antes de correrlo.
-- Solo ve tablas de seguimiento comercial/proyectos. NO ve integraciones, tokens de Outlook, RH ni documentos.
-- Para revocar todo: drop owned by patio_lectura; drop role patio_lectura;

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'patio_lectura') then
    create role patio_lectura login password 'CAMBIA-ESTA-CONTRASENA-LARGA';
  end if;
end $$;

grant usage on schema public to patio_lectura;
alter role patio_lectura set statement_timeout = '15s';
alter role patio_lectura set default_transaction_read_only = on;

do $$
declare t text;
begin
  foreach t in array array['projects','tasks','task_comments','project_comments','project_team_members',
                           'task_dependencies','deals','prospects','clients','companies','campaigns',
                           'leads','seg_items','seg_semanal','team_members']
  loop
    if to_regclass('public.' || t) is not null then
      execute format('grant select on public.%I to patio_lectura', t);
      -- las tablas tienen RLS: una política de lectura explícita solo para este rol
      execute format('drop policy if exists patio_lectura_select on public.%I', t);
      execute format('create policy patio_lectura_select on public.%I for select to patio_lectura using (true)', t);
      raise notice 'ok: %', t;
    else
      raise notice 'no existe (se omite): %', t;
    end if;
  end loop;
end $$;
