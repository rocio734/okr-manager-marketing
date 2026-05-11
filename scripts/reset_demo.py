#!/usr/bin/env python3
"""
Reset demo — limpia iniciativas, tasks, entradas y propuestas.
Deja los cycles intactos (con su kr_snapshot) pero los pone en pending_initiative_approval
para que el dashboard muestre los KRs y el botón de generar funcione de nuevo.
"""
from _etendo import sb_request

def main():
    print("=== RESET DEMO ===")

    # Borrar solo los hijos, mantener cycles
    for name, path in [
        ("progress_entries", "progress_entries?id=gt.0"),
        ("tasks",            "tasks?id=gt.0"),
        ("kr_proposals",     "kr_proposals?id=gt.0"),
        ("initiatives",      "initiatives?id=gt.0"),
    ]:
        try:
            sb_request("DELETE", path)
            print(f"  ✓ {name} borradas")
        except Exception as e:
            print(f"  ✗ Error borrando {name}: {e}")

    # Resetear ciclos a pending_initiative_approval para reusar el kr_snapshot
    try:
        sb_request("PATCH", "cycles?id=gt.0", {
            "status": "pending_initiative_approval",
            "closed_at": None,
        })
        print("  ✓ cycles reseteados a pending_initiative_approval")
    except Exception as e:
        print(f"  ✗ Error reseteando cycles: {e}")

    print("\nListo. El dashboard muestra los KRs y el botón genera nuevas iniciativas.")

if __name__ == "__main__":
    main()
