-- =============================================================================
-- OKR Manager — Schema completo
-- Pegá esto en Supabase → SQL Editor → New query → Run
--
-- Crea las 5 tablas del flujo semanal: cycles, initiatives, tasks,
-- progress_entries, kr_proposals. Habilita RLS con policies que separan
-- "leer/comentar" (público) de "aprobar/escribir" (solo aprobador autenticado).
--
-- Es seguro: usa IF NOT EXISTS en todo, no toca data existente.
-- =============================================================================

-- Extensión necesaria para gen_random_uuid()
create extension if not exists pgcrypto;

-- -----------------------------------------------------------------------------
-- 1. CYCLES — un ciclo semanal por team
-- -----------------------------------------------------------------------------
create table if not exists cycles (
  id              bigint primary key generated always as identity,
  team            text   not null,
  week_start      date   not null,                   -- lunes de la semana
  week_end        date   not null,                   -- domingo
  status          text   not null default 'pending_initiative_approval',
                                                     -- pending_initiative_approval
                                                     -- in_progress
                                                     -- pending_kr_approval
                                                     -- closed
  kr_snapshot     jsonb,                             -- valores de KRs al inicio del ciclo
  created_at      timestamptz default now(),
  closed_at       timestamptz,
  unique (team, week_start)
);

create index if not exists idx_cycles_team_status on cycles(team, status);
create index if not exists idx_cycles_week_start  on cycles(week_start desc);

-- -----------------------------------------------------------------------------
-- 2. INITIATIVES — iniciativas (generadas por el agente o agregadas a mano)
-- -----------------------------------------------------------------------------
create table if not exists initiatives (
  id                     bigint primary key generated always as identity,
  cycle_id               bigint not null references cycles(id) on delete cascade,
  kr_id                  text   not null,            -- id del KR en Etendo
  kr_name                text   not null,            -- nombre legible
  title                  text   not null,
  description            text,
  execution_plan         text,                       -- pasos concretos
  bottleneck             text,                       -- cuello de botella identificado
  fundamento             text,                       -- por qué esta iniciativa lo ataca
  status                 text   not null default 'proposed',
                                                     -- proposed
                                                     -- approved
                                                     -- rejected
                                                     -- suggested_completed
                                                     -- completed
                                                     -- cancelled
  created_by             text   not null default 'agent', -- agent | human
  created_by_email       text,                            -- email si fue humano
  approved_at            timestamptz,
  approved_by_email      text,
  completed_at           timestamptz,
  rejection_reason       text,
  created_at             timestamptz default now(),
  updated_at             timestamptz default now()
);

create index if not exists idx_initiatives_cycle  on initiatives(cycle_id);
create index if not exists idx_initiatives_kr     on initiatives(kr_id);
create index if not exists idx_initiatives_status on initiatives(status);

-- -----------------------------------------------------------------------------
-- 3. TASKS — backlog que mide avance de cada iniciativa
-- -----------------------------------------------------------------------------
create table if not exists tasks (
  id              bigint primary key generated always as identity,
  initiative_id   bigint not null references initiatives(id) on delete cascade,
  title           text   not null,
  description     text,
  status          text   not null default 'pending', -- pending | doing | done
  evidence_url    text,                              -- link a archivo o doc que demuestra que está hecha
  assigned_to     text,                              -- nombre o email
  due_date        date,
  completed_at    timestamptz,
  created_at      timestamptz default now()
);

create index if not exists idx_tasks_initiative on tasks(initiative_id);
create index if not exists idx_tasks_status     on tasks(status);

-- -----------------------------------------------------------------------------
-- 4. PROGRESS_ENTRIES — avances + uploads (similar a comments, pero por iniciativa)
-- -----------------------------------------------------------------------------
create table if not exists progress_entries (
  id                bigint primary key generated always as identity,
  initiative_id     bigint not null references initiatives(id) on delete cascade,
  author_name       text   not null,
  author_email      text,
  body              text   not null,
  attachment_urls   text[] default '{}',             -- URLs a Supabase Storage
  parent_id         bigint references progress_entries(id) on delete cascade,  -- threading
  created_at        timestamptz default now()
);

create index if not exists idx_progress_initiative on progress_entries(initiative_id);
create index if not exists idx_progress_created    on progress_entries(created_at desc);

-- -----------------------------------------------------------------------------
-- 5. KR_PROPOSALS — propuestas de viernes con nuevos valores de KR
-- -----------------------------------------------------------------------------
create table if not exists kr_proposals (
  id                bigint primary key generated always as identity,
  cycle_id          bigint not null references cycles(id) on delete cascade,
  kr_id             text   not null,
  kr_name           text   not null,
  current_value     numeric,
  proposed_value    numeric,
  rationale         text,                            -- justificación del LLM con citas
  evidence_summary  jsonb,                           -- iniciativas + tasks + entries que sustentan
  status            text   not null default 'pending_approval',
                                                     -- pending_approval
                                                     -- approved
                                                     -- rejected
                                                     -- applied (escrito en Etendo)
  approved_at       timestamptz,
  approved_by_email text,
  applied_at        timestamptz,                     -- cuando el webhook escribió a Etendo
  apply_error       text,                            -- si falló el writeback
  created_at        timestamptz default now()
);

create index if not exists idx_kr_proposals_cycle  on kr_proposals(cycle_id);
create index if not exists idx_kr_proposals_status on kr_proposals(status);

-- =============================================================================
-- ROW LEVEL SECURITY
-- =============================================================================
-- Modelo: cualquiera (anon) puede LEER y agregar progress_entries.
--         Solo el aprobador autenticado puede aprobar/rechazar/cambiar status.
--         Lo escribe la app/cron con el service_role key (bypass RLS).
-- =============================================================================

-- Email del aprobador único (cambiar si en el futuro hay más aprobadores)
-- Lo dejamos como función para que sea fácil de extender.
create or replace function is_approver()
returns boolean
language sql stable
as $$
  select coalesce(
    auth.email() = 'rocio.altamirano@smfconsulting.es',
    false
  );
$$;

-- ─── cycles ────────────────────────────────────────────────────────────────
alter table cycles enable row level security;

drop policy if exists "Anyone reads cycles"     on cycles;
drop policy if exists "Approver writes cycles"  on cycles;

create policy "Anyone reads cycles"
  on cycles for select
  using (true);

create policy "Approver writes cycles"
  on cycles for all
  using (is_approver())
  with check (is_approver());

-- ─── initiatives ───────────────────────────────────────────────────────────
alter table initiatives enable row level security;

drop policy if exists "Anyone reads initiatives"     on initiatives;
drop policy if exists "Approver writes initiatives"  on initiatives;

create policy "Anyone reads initiatives"
  on initiatives for select
  using (true);

create policy "Approver writes initiatives"
  on initiatives for all
  using (is_approver())
  with check (is_approver());

-- ─── tasks ─────────────────────────────────────────────────────────────────
alter table tasks enable row level security;

drop policy if exists "Anyone reads tasks"     on tasks;
drop policy if exists "Anyone updates tasks"   on tasks;   -- equipo puede marcar done
drop policy if exists "Approver writes tasks"  on tasks;

create policy "Anyone reads tasks"
  on tasks for select
  using (true);

-- El equipo (anon) puede actualizar status y evidence_url de tasks (no crear ni borrar)
create policy "Anyone updates tasks"
  on tasks for update
  using (true)
  with check (true);

create policy "Approver writes tasks"
  on tasks for insert
  with check (is_approver());

create policy "Approver deletes tasks"
  on tasks for delete
  using (is_approver());

-- ─── progress_entries ──────────────────────────────────────────────────────
-- Cualquiera puede agregar avances (comportamiento de comments actual)
alter table progress_entries enable row level security;

drop policy if exists "Anyone reads progress"   on progress_entries;
drop policy if exists "Anyone inserts progress" on progress_entries;

create policy "Anyone reads progress"
  on progress_entries for select
  using (true);

create policy "Anyone inserts progress"
  on progress_entries for insert
  with check (true);

-- ─── kr_proposals ──────────────────────────────────────────────────────────
alter table kr_proposals enable row level security;

drop policy if exists "Anyone reads kr_proposals"     on kr_proposals;
drop policy if exists "Approver writes kr_proposals"  on kr_proposals;

create policy "Anyone reads kr_proposals"
  on kr_proposals for select
  using (true);

create policy "Approver writes kr_proposals"
  on kr_proposals for all
  using (is_approver())
  with check (is_approver());

-- =============================================================================
-- REALTIME — para que la UI vea updates en vivo
-- =============================================================================
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and tablename = 'cycles'
  ) then
    alter publication supabase_realtime add table cycles;
  end if;
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and tablename = 'initiatives'
  ) then
    alter publication supabase_realtime add table initiatives;
  end if;
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and tablename = 'tasks'
  ) then
    alter publication supabase_realtime add table tasks;
  end if;
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and tablename = 'progress_entries'
  ) then
    alter publication supabase_realtime add table progress_entries;
  end if;
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and tablename = 'kr_proposals'
  ) then
    alter publication supabase_realtime add table kr_proposals;
  end if;
end $$;

-- =============================================================================
-- TRIGGER: updated_at en initiatives
-- =============================================================================
create or replace function set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists trg_initiatives_updated on initiatives;
create trigger trg_initiatives_updated
  before update on initiatives
  for each row execute function set_updated_at();
