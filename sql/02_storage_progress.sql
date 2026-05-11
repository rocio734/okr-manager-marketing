-- =============================================================================
-- Storage bucket "progress" para uploads de avances
-- Pegá esto en Supabase → SQL Editor → Run
-- =============================================================================

insert into storage.buckets (id, name, public)
values ('progress', 'progress', true)
on conflict (id) do nothing;

-- Cualquiera puede subir y leer (avances son colaborativos)
drop policy if exists "Public read progress"   on storage.objects;
drop policy if exists "Public upload progress" on storage.objects;

create policy "Public read progress"
  on storage.objects for select
  using (bucket_id = 'progress');

create policy "Public upload progress"
  on storage.objects for insert
  with check (bucket_id = 'progress');
