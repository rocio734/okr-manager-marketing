#!/usr/bin/env python3
"""
Script de setup — ejecutar UNA SOLA VEZ para crear la Google Sheet de Etendo Intelligence
Requiere: pip install google-auth google-auth-oauthlib google-api-python-client
"""
from google.oauth2 import service_account
from googleapiclient.discovery import build
import json, os, sys

SA_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON","")
if not SA_JSON:
    print("ERROR: Falta GOOGLE_SERVICE_ACCOUNT_JSON")
    sys.exit(1)

creds = service_account.Credentials.from_service_account_info(
    json.loads(SA_JSON),
    scopes=["https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"]
)

sheets = build("sheets","v4",credentials=creds)
drive  = build("drive","v3",credentials=creds)

# Crear la hoja
spreadsheet = sheets.spreadsheets().create(body={
    "properties": {"title": "Etendo Intelligence — Base de Leads"},
    "sheets": [
        {"properties": {"title":"Leads","index":0}},
        {"properties": {"title":"Competidores","index":1}},
        {"properties": {"title":"Resumen","index":2}},
    ]
}).execute()

sheet_id = spreadsheet["spreadsheetId"]
print(f"✅ Hoja creada: https://docs.google.com/spreadsheets/d/{sheet_id}")
print(f"   GOOGLE_SHEET_ID = {sheet_id}")

# Cabeceras de Leads
sheets.spreadsheets().values().update(
    spreadsheetId=sheet_id,
    range="Leads!A1:Q1",
    valueInputOption="USER_ENTERED",
    body={"values":[[
        "Fecha","Dominio","Empresa","Sector","Señal","Score",
        "Web","LinkedIn empresa","Nombre contacto","Cargo",
        "Email","LinkedIn contacto","Teléfono","Fuente","Contexto",
        "Estado CRM","Notas"
    ]]}
).execute()

# Cabeceras de Competidores
sheets.spreadsheets().values().update(
    spreadsheetId=sheet_id,
    range="Competidores!A1:E1",
    valueInputOption="USER_ENTERED",
    body={"values":[[
        "Fecha","Competidor","Sección","Tipo de cambio","Detalle"
    ]]}
).execute()

# Cabeceras de Resumen
sheets.spreadsheets().values().update(
    spreadsheetId=sheet_id,
    range="Resumen!A1:F1",
    valueInputOption="USER_ENTERED",
    body={"values":[[
        "Fecha","Leads nuevos","Alta calidad","Con email","Cambios competidores","Total acumulado"
    ]]}
).execute()

# Formato: cabeceras en negrita y fondo amarillo Etendo
requests_fmt = []
for sheet_idx, sheet_name in enumerate(["Leads","Competidores","Resumen"]):
    sheet_info = next(s for s in spreadsheet["sheets"]
                      if s["properties"]["title"]==sheet_name)
    sid = sheet_info["properties"]["sheetId"]
    col_count = {"Leads":17,"Competidores":5,"Resumen":6}[sheet_name]
    requests_fmt.append({
        "repeatCell": {
            "range": {"sheetId":sid,"startRowIndex":0,"endRowIndex":1,
                      "startColumnIndex":0,"endColumnIndex":col_count},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": {"red":1.0,"green":0.843,"blue":0.0},
                    "textFormat": {"bold":True,"fontSize":10},
                    "horizontalAlignment": "CENTER"
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"
        }
    })
    # Congelar primera fila
    requests_fmt.append({
        "updateSheetProperties": {
            "properties": {"sheetId":sid,"gridProperties":{"frozenRowCount":1}},
            "fields": "gridProperties.frozenRowCount"
        }
    })

sheets.spreadsheets().batchUpdate(
    spreadsheetId=sheet_id,
    body={"requests":requests_fmt}
).execute()

# Compartir con todos los que tengan el enlace (solo lectura para humanos)
drive.permissions().create(
    fileId=sheet_id,
    body={"type":"anyone","role":"reader"}
).execute()

print(f"\n📋 URL de la hoja: https://docs.google.com/spreadsheets/d/{sheet_id}/edit")
print(f"\n⚡ Añade este secret en GitHub:")
print(f"   Nombre: GOOGLE_SHEET_ID")
print(f"   Valor:  {sheet_id}")
print("\n✅ Setup completo")
