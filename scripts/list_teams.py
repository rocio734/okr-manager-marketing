#!/usr/bin/env python3
"""
Lista los teams reales que tienen Objetivos cargados en Etendo, agrupados por
período. Útil para descubrir el team_id correcto antes de correr job_monday.

Lee credenciales desde reports/okr_coach_configs/*.json (si hay configs ya
hechas con okr_coach_setup.py) o pide al usuario que corra el setup primero.
"""
from _etendo import all_team_configs, etendo_login, etendo_fetch

def main():
    configs = all_team_configs()
    if not configs:
        print("No hay configs en reports/okr_coach_configs/.")
        print("Corré primero:  python3 /home/rocio/prueba/scripts/okr_coach_setup.py")
        return
    cfg = configs[0]  # cualquier config sirve para listar (todos usan misma cuenta)
    role_id = cfg["etendo"]["role_id"]
    jwt = etendo_login(role_id)
    print("Login OK.\n")

    objs = etendo_fetch(jwt, "SMFOKR_Okr_Obj")
    by_period = {}
    for o in objs:
        per  = (o.get("period$_identifier") or "").strip()
        team = o.get("team")
        team_name = o.get("team$_identifier") or "(sin nombre)"
        if not team: continue
        by_period.setdefault(per, {}).setdefault(team, team_name)

    for per, teams in sorted(by_period.items()):
        print(f"Período: {per}")
        for team_id, team_name in teams.items():
            count = sum(1 for o in objs
                        if (o.get("period$_identifier") or "").strip() == per
                        and o.get("team") == team_id)
            print(f"  {team_id}  →  {team_name}   ({count} objetivos)")
        print()

if __name__ == "__main__":
    main()
