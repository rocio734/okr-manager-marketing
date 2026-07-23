# Etendo Intelligence Dashboard

Dashboard de inteligencia competitiva y búsqueda automática de leads para Etendo ERP España.
Se actualiza automáticamente cada mañana de lunes a viernes.

---

## Qué hace

**Competidores (Odoo, SAP Business One, Holded, Sage):**
- Detecta cambios en sus webs diariamente
- Extrae precios públicos cuando están disponibles
- Identifica nuevas funcionalidades anunciadas en sus blogs

**Leads automáticos en España:**
- Empresas que buscan implantar un ERP
- Empresas que mencionan migrar desde Odoo, Sage o SAP
- Empresas que buscan consultor o partner de ERP
- Ofertas de trabajo relacionadas con ERP

---

## Configuración — 3 pasos

### Paso 1 — Google Custom Search API (para los leads)

1. Ir a https://console.cloud.google.com
2. Crear un proyecto nuevo o usar uno existente
3. Activar la API "Custom Search API"
4. Crear credenciales → API Key → copiarla
5. Ir a https://programmablesearchengine.google.com
6. Crear un motor de búsqueda nuevo → "Buscar en toda la web" → copiar el ID

El plan gratuito permite 100 búsquedas/día — más que suficiente para este uso.

### Paso 2 — Añadir secretos en GitHub

En el repositorio: Settings → Secrets and variables → Actions → New repository secret

Añadir dos secretos:
- `GOOGLE_API_KEY` → la API key de Google
- `GOOGLE_CSE_ID`  → el ID del motor de búsqueda

**Nota:** sin estos secretos el script funciona igual pero sin buscar leads nuevos
— solo monitorea competidores.

### Paso 3 — Subir al repositorio

Subir estos archivos al repositorio conectado a Render:
- `index.html` (el dashboard)
- `fetch_intel.py` (el script)
- `.github/workflows/intel.yml` (la automatización)
- `intel_data.json` (se crea automáticamente en la primera ejecución)

Render desplegará el dashboard automáticamente cuando GitHub Actions
haga commit del `index.html` actualizado.

---

## Primera ejecución manual

En GitHub: pestaña Actions → "Etendo Intelligence — Actualización diaria" → "Run workflow"

La primera ejecución hace una foto inicial de las webs de los competidores.
A partir de la segunda ejecución empezará a detectar cambios.

---

## Estructura del dashboard

- **Resumen** — KPIs del día + alertas + preview de leads mejores
- **Competidores** — tarjetas por competidor con cambios y novedades
- **Precios** — tabla comparativa de precios extraídos automáticamente
- **Leads** — tabla completa con filtros por tipo de señal y score

---

## Coste

- GitHub Actions: gratuito (2.000 minutos/mes en plan free)
- Google Custom Search: gratuito hasta 100 búsquedas/día
- Render: según plan actual
- **Total adicional: $0**
