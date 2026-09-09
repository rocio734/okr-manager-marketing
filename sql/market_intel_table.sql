-- Tabla para resultados de Visión del Mercado
CREATE TABLE IF NOT EXISTS market_intel (
  id           BIGSERIAL PRIMARY KEY,
  team         TEXT NOT NULL DEFAULT 'marketing',
  generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  serp_data    JSONB,
  analysis     JSONB
);

ALTER TABLE market_intel ENABLE ROW LEVEL SECURITY;
CREATE POLICY "read_market_intel" ON market_intel FOR SELECT USING (true);
CREATE POLICY "insert_market_intel" ON market_intel FOR INSERT WITH CHECK (true);

-- Datos reales de Google Search Console / GA4 / Ads (además de SerpAPI).
-- analysis.metricas_semana ya trae el resumen de KPIs; estas columnas
-- guardan la respuesta completa de cada API para trazabilidad/debug.
ALTER TABLE market_intel ADD COLUMN IF NOT EXISTS gsc_data JSONB;
ALTER TABLE market_intel ADD COLUMN IF NOT EXISTS ga4_data JSONB;
ALTER TABLE market_intel ADD COLUMN IF NOT EXISTS ads_data JSONB;
