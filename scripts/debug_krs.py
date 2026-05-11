#!/usr/bin/env python3
"""Debug — muestra campos reales de los KRs para identificar el campo que
liga al objetivo padre."""
import json
from _etendo import all_team_configs, etendo_login, etendo_fetch

def main():
    cfg  = all_team_configs()[0]
    jwt  = etendo_login(cfg["etendo"]["role_id"])
    krs  = etendo_fetch(jwt, "SMFOKR_Okr_Kr")
    print(f"Total KRs: {len(krs)}\n")
    if not krs:
        print("Sin KRs en Etendo.")
        return

    print("=== CAMPOS DEL PRIMER KR ===")
    print(json.dumps(krs[0], indent=2, default=str))

    # Mostrar campos únicos que contengan 'obj' o 'objective' (el link al padre)
    print("\n=== Campos sospechosos (obj/objective/parent) ===")
    seen = set()
    for kr in krs:
        for k, v in kr.items():
            if any(x in k.lower() for x in ("obj", "objective", "parent", "okr")):
                seen.add((k, str(v)[:60]))
    for k, v in sorted(seen):
        print(f"  {k} = {v}")

    # Cruzar con los 5 obj IDs de Marketing Q2 2026 del debug anterior
    marketing_q2_ids = {
        "AB9F1B621F484C1CA7A9DCB5ED7EFCFE",
        "8E4CCA0B1E954F11841E79FDEDE2E94F",
        "25688CAF9EC244A9B3FA44A5D91D82E2",
        "1EB8E3937FAE4B44A3BBDA7A42EE77AD",
        "13C215988DDA4A3D9BBD44561D64C7F0",
    }
    print("\n=== KRs que pertenecen a esos 5 objetivos (buscando en todos los campos) ===")
    found = 0
    for kr in krs:
        for k, v in kr.items():
            if str(v) in marketing_q2_ids:
                print(f"  kr.id={kr.get('id','?')[:8]}  campo={k}  valor={v}")
                found += 1
                break
    print(f"Total encontrados: {found}")

if __name__ == "__main__":
    main()
