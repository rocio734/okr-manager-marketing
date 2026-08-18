# n8n — Workflow: Clasificador de Auto-Replies

## Qué hace
Monitorea el inbox de `victoria.miguez@smfconsulting.es` cada 15 minutos,
detecta bounces y out-of-office, y llama al webhook de registro para que
`process_autoreplies.py` actualice el tracking.

---

## Nodos (en orden)

### 1. Gmail Trigger
- **Tipo**: Gmail Trigger
- **Cuenta**: victoria.miguez@smfconsulting.es
- **Evento**: Message Received
- **Filtros**: Solo bandeja de entrada (`INBOX`)
- **Poll cada**: 15 minutos

### 2. IF — ¿Es auto-reply?
- **Tipo**: IF
- **Condición**: ANY de estas (subject en minúsculas contiene):
  - `mail delivery`
  - `undelivered`
  - `undeliverable`
  - `mailer-daemon`
  - `delivery failed`
  - `no se pudo entregar`
  - `out of office`
  - `fuera de`
  - `vacaciones`
  - `ausente`
  - `respuesta automática`
  - `automatic reply`
  - `autoreply`
- **Si FALSE**: Stop (ignorar email normal)

### 3. Switch — ¿Bounce o Vacaciones?
- **Tipo**: Switch
- **Valor**: `{{ $json.subject.toLowerCase() }}`
- **Caso 1** (bounce): contiene alguno de:
  `mail delivery`, `undelivered`, `undeliverable`, `delivery failed`, `mailer-daemon`, `no se pudo entregar`
  → Output: `bounce`
- **Caso 2** (vacaciones): el resto
  → Output: `vacaciones`

### 4a. Set — Bounce
- **email**: `{{ $json.from.value[0].address }}`
  *(para bounce, el from suele ser el destino original — verificar en logs reales)*
  *(alternativa: regex sobre el body para extraer el email que falló)*
- **tipo**: `bounce`
- **subject**: `{{ $json.subject }}`

### 4b. Set — Vacaciones
- **email**: `{{ $json.from.value[0].address }}`
  *(para OOO el from ES el email al que enviamos)*
- **tipo**: `vacaciones`
- **subject**: `{{ $json.subject }}`
- **fecha_vuelta**: `{{ $json.text }}` *(el body, para que process_autoreplies.py extraiga la fecha)*

### 5. HTTP Request — Registrar en webhook
- **Tipo**: HTTP Request
- **Método**: POST
- **URL**: `https://n8n.labs.etendo.cloud/webhook/NUEVO_WEBHOOK_REGISTRO`
  *(crear un segundo webhook en n8n — ver abajo)*
- **Body (JSON)**:
```json
{
  "email": "{{ $json.email }}",
  "tipo": "{{ $json.tipo }}",
  "raw_subject": "{{ $json.subject }}",
  "raw_from": "{{ $json.from }}",
  "fecha_vuelta": "{{ $json.fecha_vuelta }}"
}
```

---

## Webhook receptor (segundo workflow n8n)

### Nombre: `Outreach — Registro Auto-Reply`
### Nodo 1: Webhook
- **Path**: `/registro-autoreply`
- **Método**: POST
- Recibe: `{ email, tipo, raw_subject, raw_from, fecha_vuelta }`

### Nodo 2: Execute Command
- **Comando**:
```bash
cd /ruta/al/proyecto && python3 process_autoreplies.py --add \
  "{{ $json.body.email }}" \
  "{{ $json.body.tipo }}" \
  "{{ $json.body.raw_subject }}"
```

**Alternativa si n8n no tiene acceso al servidor:**
Guardar en Google Sheets y correr `sync_from_sheets.py` manualmente o en cron.

---

## Casos especiales — bounces

Para bounces, el `From` suele ser `mailer-daemon@dominio.com` o `postmaster@...`.
El email original que falló está en el **body** del bounce, generalmente con:
- `"Original-Recipient: rfc822; EMAIL"`
- `"Final-Recipient: rfc822; EMAIL"`
- `"To: EMAIL"`

**Regex recomendado en n8n (Code node):**
```javascript
const body = $input.item.json.text || '';
const match = body.match(/(?:Final-Recipient|Original-Recipient):\s*rfc822;\s*([\w.@+-]+)/i)
           || body.match(/could not be delivered to:\s*([\w.@+-]+)/i)
           || body.match(/unknown user[:\s]+([\w.@+-]+)/i);
const email = match ? match[1] : $input.item.json.from?.value?.[0]?.address || '';
return [{ json: { ...$input.item.json, email_extraido: email } }];
```

---

## Cómo probar manualmente

Sin n8n, podés registrar un auto-reply a mano:

```bash
# Bounce
python3 process_autoreplies.py --add "email@empresa.com" "bounce" "Mail delivery failed"

# Vacaciones
python3 process_autoreplies.py --add "email@empresa.com" "vacaciones" "Out of office until Sep 1"

# Procesar pendientes
python3 process_autoreplies.py

# Ver estado
python3 process_autoreplies.py --show
```
