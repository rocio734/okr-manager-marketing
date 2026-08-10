-- =============================================================================
-- RPC: approve_initiatives_batch
--
-- Bypasea RLS via SECURITY DEFINER. Mismo patrón que approve_kr_proposals.
-- Maneja aprobaciones, rechazos, edits in-line e iniciativas manuales.
-- =============================================================================

create or replace function approve_initiatives_batch(
  p_approve_ids   bigint[],
  p_reject_ids    bigint[],
  p_cycle_id      int,
  p_approver      text,
  p_edits         jsonb,   -- [{id, title, execution_plan}]
  p_manual_inits  jsonb    -- [{kr_id, kr_name, title, execution_plan, bottleneck}]
)
returns json
language plpgsql
security definer
set search_path = public

as $$
declare
  v_email  text := lower(coalesce(auth.email(), ''));
  v_expect text := lower(p_approver);
  v_now    timestamptz := now();
  v_edit   jsonb;
  v_init   jsonb;
begin
  -- Validar aprobador
  if v_email = '' or v_email != v_expect then
    raise exception 'Unauthorized: solo el aprobador puede ejecutar esta acción (got: %)', v_email;
  end if;

  -- Aplicar edits in-line (título y plan) antes de cambiar status
  if p_edits is not null then
    for v_edit in select * from jsonb_array_elements(p_edits)
    loop
      update initiatives
      set title          = coalesce(v_edit->>'title',          title),
          execution_plan = coalesce(v_edit->>'execution_plan', execution_plan)
      where id = (v_edit->>'id')::bigint;
    end loop;
  end if;

  -- Aprobar iniciativas seleccionadas
  if p_approve_ids is not null and array_length(p_approve_ids, 1) > 0 then
    update initiatives
    set status             = 'approved',
        approved_at        = v_now,
        approved_by_email  = v_email
    where id = any(p_approve_ids);
  end if;

  -- Rechazar iniciativas
  if p_reject_ids is not null and array_length(p_reject_ids, 1) > 0 then
    update initiatives
    set status           = 'rejected',
        rejection_reason = 'Rechazada en aprobación de lunes'
    where id = any(p_reject_ids);
  end if;

  -- Insertar iniciativas manuales
  if p_manual_inits is not null then
    for v_init in select * from jsonb_array_elements(p_manual_inits)
    loop
      insert into initiatives
        (cycle_id, kr_id, kr_name, title, execution_plan, bottleneck,
         status, created_by, created_by_email, approved_at, approved_by_email)
      values
        (p_cycle_id,
         v_init->>'kr_id', v_init->>'kr_name',
         v_init->>'title', coalesce(v_init->>'execution_plan', ''),
         coalesce(v_init->>'bottleneck', ''),
         'approved', 'human', v_email, v_now, v_email);
    end loop;
  end if;

  -- Avanzar ciclo a in_progress
  update cycles
  set status = 'in_progress'
  where id = p_cycle_id;

  return json_build_object('ok', true, 'approved', coalesce(array_length(p_approve_ids, 1), 0));
end;
$$;

grant execute on function approve_initiatives_batch(bigint[], bigint[], int, text, jsonb, jsonb) to anon, authenticated;
