# OKR Manager — Marketing

Sistema que cierra el ciclo de OKRs semanal: el agente lee KRs de Etendo, propone iniciativas, el equipo carga avances, el agente propone nuevos valores y los escribe de vuelta a Etendo tras aprobación humana.

## Estructura

```
okr_manager_site/
├── sql/
│   ├── 01_schema.sql          → 5 tablas + RLS + realtime + triggers
│   └── 02_storage_progress.sql → bucket de uploads
├── scripts/
│   ├── job_monday.py            → Lunes 9am: lee KRs + genera iniciativas
│   ├── job_generate_backlog.py  → Tras aprobación: genera tasks
│   ├── job_daily_sweep.py       → Diario 8pm: detecta iniciativas completas
│   ├── job_friday.py            → Viernes 5pm: propone nuevos KR values
│   └── job_writeback.py         → Cada 5 min: aplica approved a Etendo
├── .github/workflows/cron.yml   → Schedules de los jobs
├── config.js                    → Config del front (URL Supabase + anon)
├── styles.css                   → Estilo compartido
├── app.js                       → Auth (magic link) + topbar + helpers
├── dashboard.html               → Vista principal: iniciativas por KR
├── initiative.html              → Detail: plan + tasks + avances + uploads
├── approval-monday.html         → Batch approval lunes
├── kr-proposals-friday.html     → Aprobación viernes de nuevos KR values
└── index.html                   → Redirect → dashboard
```

## Setup paso a paso

### 1. Supabase

1. Crear proyecto en supabase.com (o usar el existente).
2. **SQL Editor → New query** → pegar `sql/01_schema.sql` → Run.
3. **SQL Editor → New query** → pegar `sql/02_storage_progress.sql` → Run.
4. **Authentication → Settings**:
   - Enable email provider
   - Disable signups (opcional)
   - **Email templates → Magic Link** → personalizar (opcional)
   - **URL Configuration → Site URL**: la URL del Render site (ej. `https://okr-marketing.onrender.com`)
5. Copiar `Project URL` y `anon public` key → pegarlos en `config.js`.
6. Copiar `service_role` key → guardarla para los secrets de GitHub.

### 2. Aprobador único

El aprobador está hardcodeado en `is_approver()` (función SQL) y en `app.js`/`config.js`. Si querés cambiarlo:
- En SQL: `create or replace function is_approver()` con el nuevo email.
- En `config.js`: campo `approver_email`.

### 3. GitHub Actions

El repo donde pusheás este directorio necesita los siguientes **secrets** (Settings → Secrets and variables → Actions):

| Secret | Valor |
|---|---|
| `ETENDO_USERNAME` | tu usuario de Etendo |
| `ETENDO_PASSWORD` | password |
| `SUPABASE_URL` | `https://xxx.supabase.co` |
| `SUPABASE_SERVICE_KEY` | service_role key (bypasea RLS) |
| `ANTHROPIC_API_KEY` | API key de Claude |
| `RESEND_API_KEY` | API key de Resend para emails |

Y los siguientes **variables**:

| Variable | Valor |
|---|---|
| `OKR_APPROVER_EMAIL` | `rocio.altamirano@smfconsulting.es` |
| `OKR_SITE_URL` | URL del Render site (sin trailing slash) |

### 4. Render Static Site

1. Crear nuevo Static Site en Render apuntando al repo.
2. Build command: vacío (es estático).
3. Publish directory: la raíz del directorio `okr_manager_site/`.
4. Domain: el que quieras (ej. `okr-marketing.onrender.com`).

### 5. Resend

1. Verificar dominio `smfconsulting.es` en resend.com.
2. Configurar DNS: SPF, DKIM, DMARC según indique Resend.
3. La dirección remitente es `okr@smfconsulting.es` (cambiar en los scripts si querés otra).

## Flujo de uso

**Lunes 9am** (automático):
1. GitHub Actions corre `job_monday.py`.
2. Lee KRs Marketing de Etendo, crea cycle, genera iniciativas via LLM.
3. Email con link a `/approval-monday.html`.

**Lunes durante el día** (Rocío):
1. Hace click en el mail → magic link → loguea.
2. Revisa iniciativas, edita, agrega/quita, **Aprobar batch**.
3. El cycle pasa a `in_progress`. Tras unos minutos, `job_generate_backlog.py` genera tasks.

**Lun-Jue** (equipo Marketing):
1. Entra a `/dashboard.html`, click en una iniciativa.
2. Carga avances (texto + uploads). Marca tasks done.
3. Cada noche `job_daily_sweep.py` evalúa si hay iniciativas terminadas.

**Viernes 5pm** (automático):
1. `job_friday.py` agrega evidencia, genera propuestas de KR.
2. Email con link a `/kr-proposals-friday.html`.

**Viernes/Lunes** (Rocío):
1. Revisa propuestas, ajusta valores, **Aprobar y escribir en Etendo**.
2. `job_writeback.py` (cada 5 min) lo aplica via SmartClient.
3. Cycle pasa a `closed`.

**Lunes siguiente** → arranca cycle nuevo con los valores actualizados.

## Multi-team (después)

Para agregar otro team (Servicios, Producto, etc.):

1. Crear `scripts/teams.json`:
   ```json
   [
     { "team_id": "ID_MARKETING_EN_ETENDO", "team_name": "marketing" },
     { "team_id": "ID_SERVICIOS_EN_ETENDO", "team_name": "servicios" }
   ]
   ```
2. Crear un Render site por team con su propio `config.js` (campo `team`).
3. Compartir el mismo Supabase — el filtro `team` en queries hace el resto.

## Comandos útiles

```bash
# Probar localmente sin escribir nada
python3 scripts/job_monday.py --dry-run --team marketing

# Forzar generación de backlog para iniciativas approved
python3 scripts/job_generate_backlog.py

# Triggerar manualmente desde GitHub Actions
# Actions → "OKR Manager — cron jobs" → Run workflow → elegir job
```

## Troubleshooting

**El mail no llega:** verificar dominio en Resend + DNS records propagados.

**El job lunes corre pero no inserta nada:** revisar que el `team_id` esté correcto en `teams.json` o `OKR_TEAM_ID_MARKETING`. También que el período `Q2 2026` exista en Etendo.

**El writeback falla con CSRF:** la función `login_and_get_session()` usa Playwright headless. Si Etendo cambia el login flow, hay que ajustar los selectores.

**No se ve el botón "Aprobar":** el usuario logueado no es el aprobador. Verificar `is_approver()` SQL function y `auth.email()` del session.
