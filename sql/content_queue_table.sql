CREATE TABLE IF NOT EXISTS content_queue (
  id            BIGSERIAL PRIMARY KEY,
  week_start    DATE        NOT NULL,
  platform      TEXT        NOT NULL,  -- 'linkedin', 'instagram'
  format        TEXT        NOT NULL,  -- 'post', 'carousel', 'reel'
  sector        TEXT        NOT NULL,  -- 'manufacturing', 'distribution', 'retail', 'services', 'general'
  day_slot      TEXT,                  -- 'monday'...'friday'
  source_type   TEXT,                  -- 'gartner', 'tedx', 'industry', 'product'
  source_title  TEXT,
  source_url    TEXT,
  title         TEXT,
  body          TEXT,
  slides        JSONB,                 -- [{title, body}] para carruseles
  hashtags      TEXT[],
  status        TEXT        NOT NULL DEFAULT 'draft',  -- draft → approved → published
  approved_by     TEXT,
  approved_at     TIMESTAMPTZ,
  published_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- Added post-deploy (schema drift columns):
  image_prompt    TEXT,
  image_url       TEXT,
  rejection_note  TEXT,
  metrics         JSONB      -- {impressions, likes, comments, clicks}
);

ALTER TABLE content_queue ENABLE ROW LEVEL SECURITY;
CREATE POLICY "read_content_queue"   ON content_queue FOR SELECT USING (true);
CREATE POLICY "insert_content_queue" ON content_queue FOR INSERT WITH CHECK (true);
CREATE POLICY "update_content_queue" ON content_queue FOR UPDATE USING (true);
CREATE POLICY "delete_content_queue" ON content_queue FOR DELETE USING (true);
