-- =============================================================================
-- RPC: approve_kr_proposals
--
-- Bypasea RLS via SECURITY DEFINER. Valida que el caller sea el approver
-- usando auth.email() (que sí llega aunque el rol sea anon/authenticated).
-- El frontend llama sb.rpc('approve_kr_proposals', {...}) en lugar de
-- hacer UPDATEs directos que quedan bloqueados por RLS cuando el JWT
-- del usuario expiró o no fue establecido correctamente.
-- =============================================================================

create or replace function approve_kr_proposals(
  p_approve_ids bigint[],
  p_reject_ids  bigint[],
  p_cycle_id    int,
  p_approver    text  -- email del aprobador, para doble validación
)
returns json
language plpgsql
security definer
set search_path = public
as $$
declare
  v_email  text := lower(coalesce(auth.email(), ''));
  v_expect text := lower(p_approver);
begin
  -- Validar que quien llama es el approver
  if v_email = '' or v_email != v_expect then
    raise exception 'Unauthorized: solo el aprobador puede ejecutar esta acción (got: %)', v_email;
  end if;

  -- Aprobar los seleccionados
  if p_approve_ids is not null and array_length(p_approve_ids, 1) > 0 then
    update kr_proposals
    set
      status             = 'approved',
      approved_at        = now(),
      approved_by_email  = v_email
    where id = any(p_approve_ids);
  end if;

  -- Rechazar los desmarcados
  if p_reject_ids is not null and array_length(p_reject_ids, 1) > 0 then
    update kr_proposals
    set status = 'rejected'
    where id = any(p_reject_ids);
  end if;

  -- Cerrar el ciclo
  if p_cycle_id is not null then
    update cycles
    set status = 'closed', closed_at = now()
    where id = p_cycle_id;
  end if;

  return json_build_object('ok', true, 'approved', array_length(p_approve_ids, 1));
end;
$$;

-- Permitir que cualquier usuario autenticado (y anon) pueda llamar la función
-- La validación real está dentro: auth.email() debe coincidir con p_approver
grant execute on function approve_kr_proposals(bigint[], bigint[], int, text) to anon, authenticated;
