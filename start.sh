#!/bin/bash
# Arranca el site OKR Manager completo (servidor estático + backend local)
# Uso: ./start.sh
# Después abrí: http://localhost:8080

cd "$(dirname "$0")"

echo "Iniciando OKR Manager..."
echo ""

# Backend local (writeback a Etendo) en puerto 8081
python3 scripts/local_backend.py &
BACKEND_PID=$!

# Servidor estático del site en puerto 8080
python3 -m http.server 8080 &
SITE_PID=$!

echo "✓ Site:    http://localhost:8080"
echo "✓ Backend: http://localhost:8081"
echo ""
echo "Presioná Ctrl+C para detener todo."

# Al cerrar con Ctrl+C, matar ambos procesos
trap "kill $BACKEND_PID $SITE_PID 2>/dev/null; echo 'Servidores detenidos.'" EXIT
wait
