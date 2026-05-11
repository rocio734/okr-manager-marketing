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
