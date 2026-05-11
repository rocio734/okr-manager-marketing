#!/usr/bin/env python3
"""
Debug — imprime los primeros Objetivos de Etendo con todos sus campos
para identificar el nombre real del campo team y el formato del período.
"""
import json
from _etendo import all_team_configs, etendo_login, etendo_fetch

def main():
    cfg = all_team_configs()[0]
    role_id = cfg["etendo"]["role_id"]
    expected_team = cfg["team"]["id"]
    expected_period = cfg["period"]["name"]
    print(f"Team esperado: {expected_team}  ({cfg['team']['name']})")
    print(f"Período esperado: '{expected_period}'\n")

    jwt = etendo_login(role_id)
    objs = etendo_fetch(jwt, "SMFOKR_Okr_Obj")
    print(f"Total objetivos: {len(objs)}\n")

    if not objs:
        print("Sin objetivos en Etendo.")
        return

    # Mostrar todos los campos del primer objetivo
    print("=== CAMPOS DEL PRIMER OBJETIVO ===")
    print(json.dumps(objs[0], indent=2, default=str))

    # Mostrar valores únicos de los campos sospechosos
    teams_seen   = set()
    periods_seen = set()
    for o in objs:
        for k, v in o.items():
            if "team" in k.lower():
                teams_seen.add((k, str(v)[:50]))
            if "period" in k.lower():
                periods_seen.add((k, str(v)[:50]))

    print("\n=== Campos relacionados con TEAM ===")
    for k, v in sorted(teams_seen):
        print(f"  {k} = {v}")

    print("\n=== Campos relacionados con PERIOD ===")
    for k, v in sorted(periods_seen):
        print(f"  {k} = {v}")

    # ¿Cuántos matchean exactamente?
    matches_team   = [o for o in objs if o.get("team") == expected_team]
    matches_period = [o for o in objs if (o.get("period$_identifier") or "").strip() == expected_period]
    print(f"\nMatchean team {expected_team}: {len(matches_team)}")
    print(f"Matchean period '{expected_period}': {len(matches_period)}")

    # Cross-reference: qué períodos tienen los objetos del Marketing team
    print(f"\n=== Períodos de los {len(matches_team)} objetivos del team Marketing ===")
    for o in matches_team:
        pid = o.get("period$_identifier") or o.get("period") or "(sin período)"
        print(f"  id={o.get('id','?')[:8]}  name={o.get('name','?')[:40]}  period={pid}")

if __name__ == "__main__":
    main()
