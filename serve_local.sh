#!/usr/bin/env bash
# Sirve el site local en http://localhost:8080
# Después abrí: http://localhost:8080/dashboard.html
cd "$(dirname "$0")"
echo "Sirviendo en http://localhost:8080  →  abrí http://localhost:8080/dashboard.html"
echo "Ctrl+C para parar"
python3 -m http.server 8080
