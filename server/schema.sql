-- ============================================================
-- 阿赖耶记忆引擎 · 数据基座（SQLite 单文件）
--
-- 设计原则：种子指纹不可变（修正走新种子+关联，永不改内容）
--           熏习算子只作用 weight/state，永不碰语义内容
--           单表种子容器：检索/熏习/索引单点收口，可延展
-- 基础表：xd_users / xd_ais / xd_voices / xd_speaker / user_tokens / me_c_tones
--        （用户 / AI 本体 / 音色 / 声纹 / 登录令牌 / 情绪音色锚点）
-- ============================================================

-- ══════════ 基础：账号与本体 ══════════
CREATE TABLE IF NOT EXISTS xd_users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT NOT NULL UNIQUE,
  password      TEXT NOT NULL,                    -- pbkdf2_sha256$迭代$盐$摘要
  nickname      TEXT,
  phone         TEXT,
  email         TEXT,
  avatar        TEXT DEFAULT '',
  avatar_url    TEXT DEFAULT '',
  call_me       TEXT DEFAULT '',
  status        INTEGER DEFAULT 1,                -- 1 正常 / 0 停用
  role          TEXT DEFAULT 'user',
  create_date   TEXT DEFAULT (datetime('now','localtime')),
  lastlogin     TEXT,
  logins        INTEGER DEFAULT 0,
  loginip       TEXT,
  language      TEXT DEFAULT 'zh',
  profession    TEXT DEFAULT '',
  industry      TEXT,
  city          TEXT DEFAULT '',
  gender        TEXT DEFAULT '',
  age           INTEGER,
  birthday      TEXT DEFAULT '',
  zodiac        TEXT,
  chinese_zodiac TEXT,
  interests     TEXT DEFAULT '',
  life_status   TEXT DEFAULT '',
  life_stage    TEXT,
  department    TEXT,
  education     TEXT,
  school        TEXT,
  current_voice_id INTEGER,
  ai_me_id      INTEGER,                          -- 唯一权威：用户的 AI 本体
  is_admin      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_users_ai_me ON xd_users(ai_me_id);

CREATE TABLE IF NOT EXISTS xd_ais (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       INTEGER NOT NULL DEFAULT 0,       -- 0 = 公共 AI（如「如意」）
  call_me       TEXT DEFAULT '',
  name          TEXT NOT NULL,
  voice_id      TEXT NOT NULL DEFAULT '',         -- 外部 TTS 的音色 id（厂商自己的 voice 名）
  is_alive      INTEGER DEFAULT 1,
  is_public     INTEGER DEFAULT 0,                -- 1 = 所有登录用户可对话的公共本体
  sync_with_user INTEGER DEFAULT 0,
  lang          TEXT NOT NULL DEFAULT 'zh',
  avatar        TEXT DEFAULT '',
  ai_avatar_url TEXT DEFAULT '',
  ai_identity   TEXT,
  ai_personality TEXT,
  ai_bio        TEXT,
  identity_tags TEXT,
  expertise_tags TEXT,
  personality_tags TEXT,
  gender        TEXT,
  created_at    TEXT DEFAULT (datetime('now','localtime')),
  updated_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_ais_user ON xd_ais(user_id);
CREATE INDEX IF NOT EXISTS idx_ais_public ON xd_ais(is_public);

CREATE TABLE IF NOT EXISTS xd_voices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       INTEGER NOT NULL DEFAULT 0,
  voice_name    TEXT NOT NULL,
  audio_path    TEXT,
  embedding     BLOB,
  transcript    TEXT,
  quality       TEXT,
  public        INTEGER DEFAULT 0,
  status        INTEGER DEFAULT 1,
  en_name       TEXT,
  wakeword      TEXT,
  language      TEXT DEFAULT 'zh',
  description   TEXT,
  mode          TEXT DEFAULT 'external',
  emoji         TEXT DEFAULT '',
  gender        TEXT DEFAULT 'unknown',
  tags          TEXT DEFAULT '',
  accent        TEXT DEFAULT '#3b82f6',
  glow          TEXT DEFAULT 'rgba(59,130,246,0.25)',
  bg            TEXT DEFAULT 'rgba(59,130,246,0.15)',
  audio_hash    TEXT,
  is_owner_voice INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT DEFAULT (datetime('now','localtime')),
  updated_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_voices_user ON xd_voices(user_id);

CREATE TABLE IF NOT EXISTS xd_speaker (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nickname      TEXT NOT NULL,
  identity      TEXT,
  personality   TEXT,
  memory        TEXT,
  avatar        TEXT,
  is_registered_user INTEGER DEFAULT 0,
  user_id       INTEGER,
  embedding     BLOB,
  sample_count  INTEGER DEFAULT 0,
  created_at    TEXT DEFAULT (datetime('now','localtime')),
  updated_at    TEXT DEFAULT (datetime('now','localtime')),
  last_active_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_speaker_user ON xd_speaker(user_id);

CREATE TABLE IF NOT EXISTS user_tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       INTEGER NOT NULL,
  token         TEXT NOT NULL UNIQUE,
  created_at    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON user_tokens(user_id);

-- 情绪音色锚点（情绪词 → 音色 id；留空则全部用默认音色）
CREATE TABLE IF NOT EXISTS me_c_tones (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tag           TEXT NOT NULL,
  voice_id      INTEGER NOT NULL,
  suitability   TEXT NOT NULL DEFAULT '',
  ai_id         INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL DEFAULT 'active',
  created_at    TEXT DEFAULT (datetime('now','localtime')),
  updated_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_tones_ai ON me_c_tones(ai_id, tag);

-- ══════════ 第八识·阿赖耶识：种子主表 ══════════
CREATE TABLE IF NOT EXISTS alaya_seeds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  seed_type     TEXT NOT NULL DEFAULT 'value',
  activation    REAL NOT NULL DEFAULT 0.0,
  seed_text     TEXT NOT NULL,
  raw_text      TEXT,
  owner_ai_id   INTEGER NOT NULL,
  peer_ai_id    INTEGER NOT NULL DEFAULT 0,
  subject       TEXT NOT NULL DEFAULT 'self',
  person_id     INTEGER NOT NULL DEFAULT 0,
  concept_id    INTEGER NOT NULL DEFAULT 0,
  seed_date     TEXT,
  time_fuzzy    TEXT,
  emotion_tone_id INTEGER,
  emotion_word  TEXT,
  scene         TEXT,
  life_stage    TEXT,
  sense_id      TEXT DEFAULT 'voice',
  episode_id    TEXT,
  confidence    REAL NOT NULL DEFAULT 0.9,
  weight        REAL NOT NULL DEFAULT 1.0,
  recall_count  INTEGER NOT NULL DEFAULT 0,
  last_recalled_at TEXT,
  state         TEXT NOT NULL DEFAULT 'confirmed',
  superseded_by INTEGER,
  evidence      TEXT,
  expire_at     TEXT,
  created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_seed_owner_peer ON alaya_seeds(owner_ai_id, peer_ai_id, state);
CREATE INDEX IF NOT EXISTS idx_seed_owner_act ON alaya_seeds(owner_ai_id, activation);
CREATE INDEX IF NOT EXISTS idx_seed_expire ON alaya_seeds(expire_at);
CREATE INDEX IF NOT EXISTS idx_seed_owner_date ON alaya_seeds(owner_ai_id, seed_date);
CREATE INDEX IF NOT EXISTS idx_seed_owner_scene ON alaya_seeds(owner_ai_id, scene);
CREATE INDEX IF NOT EXISTS idx_seed_owner_stage ON alaya_seeds(owner_ai_id, life_stage, state);
CREATE INDEX IF NOT EXISTS idx_seed_owner_type ON alaya_seeds(owner_ai_id, seed_type, state);
CREATE INDEX IF NOT EXISTS idx_seed_owner_emo ON alaya_seeds(owner_ai_id, emotion_tone_id);
CREATE INDEX IF NOT EXISTS idx_seed_concept ON alaya_seeds(concept_id);
CREATE INDEX IF NOT EXISTS idx_seed_person ON alaya_seeds(person_id);
CREATE INDEX IF NOT EXISTS idx_seed_episode ON alaya_seeds(episode_id);

CREATE TABLE IF NOT EXISTS alaya_seed_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_ai_id   INTEGER NOT NULL,
  src_seed_id   INTEGER NOT NULL,
  dst_seed_id   INTEGER NOT NULL,
  relation_type TEXT NOT NULL DEFAULT 'co',
  weight        REAL NOT NULL DEFAULT 0.3,
  created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_seed_edge
  ON alaya_seed_edges(owner_ai_id, src_seed_id, dst_seed_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_seedge_src ON alaya_seed_edges(owner_ai_id, src_seed_id);
CREATE INDEX IF NOT EXISTS idx_seedge_dst ON alaya_seed_edges(owner_ai_id, dst_seed_id);

-- ══════════ 习气网络·概念层 ══════════
CREATE TABLE IF NOT EXISTS alaya_concepts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_ai_id   INTEGER NOT NULL,
  parent_id     INTEGER NOT NULL DEFAULT 0,
  name          TEXT NOT NULL,
  branch        TEXT NOT NULL DEFAULT 'fact',
  description   TEXT,
  aliases       TEXT,
  weight        REAL NOT NULL DEFAULT 1.0,
  status        TEXT NOT NULL DEFAULT 'active',
  created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_concept ON alaya_concepts(owner_ai_id, name);
CREATE INDEX IF NOT EXISTS idx_concept_branch ON alaya_concepts(owner_ai_id, branch, status);
CREATE INDEX IF NOT EXISTS idx_concept_parent ON alaya_concepts(parent_id);

CREATE TABLE IF NOT EXISTS alaya_concept_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_ai_id   INTEGER NOT NULL,
  src           INTEGER NOT NULL,
  dst           INTEGER NOT NULL,
  relation_type TEXT NOT NULL DEFAULT 'co',
  weight        REAL NOT NULL DEFAULT 0.3,
  auto_created  INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_cedge
  ON alaya_concept_edges(owner_ai_id, src, dst, relation_type);
CREATE INDEX IF NOT EXISTS idx_cedge_src ON alaya_concept_edges(owner_ai_id, src);
CREATE INDEX IF NOT EXISTS idx_cedge_dst ON alaya_concept_edges(owner_ai_id, dst);

-- ══════════ 第七识·末那识：我识三层 ══════════
CREATE TABLE IF NOT EXISTS alaya_self_model (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ai_id         INTEGER NOT NULL,
  peer_ai_id    INTEGER NOT NULL DEFAULT 0,
  layer         TEXT NOT NULL,
  content       TEXT NOT NULL,
  version       INTEGER NOT NULL DEFAULT 1,
  updated_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_self ON alaya_self_model(ai_id, peer_ai_id, layer);

CREATE TABLE IF NOT EXISTS alaya_self_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ai_id         INTEGER NOT NULL,
  peer_ai_id    INTEGER NOT NULL DEFAULT 0,
  layer         TEXT NOT NULL,
  version       INTEGER NOT NULL,
  content       TEXT NOT NULL,
  trigger_by    TEXT,
  change_summary TEXT,
  created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_selfver ON alaya_self_versions(ai_id, peer_ai_id, layer, version);

-- ══════════ 对话原文流水 ══════════
CREATE TABLE IF NOT EXISTS alaya_dialogues (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_ai_id   INTEGER NOT NULL,
  peer_ai_id    INTEGER NOT NULL,
  role          TEXT NOT NULL,
  content       TEXT NOT NULL,
  episode_id    TEXT,
  created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_dlg_pair ON alaya_dialogues(owner_ai_id, peer_ai_id, created_at);

-- ══════════ 熏习日志 ══════════
CREATE TABLE IF NOT EXISTS alaya_xixi_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  seed_id       INTEGER,
  concept_id    INTEGER,
  operator      TEXT NOT NULL,
  delta         REAL,
  reason        TEXT,
  created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_xixi_seed ON alaya_xixi_log(seed_id);
CREATE INDEX IF NOT EXISTS idx_xixi_time ON alaya_xixi_log(created_at);

-- ══════════ 人物表 ══════════
CREATE TABLE IF NOT EXISTS alaya_persons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_ai_id   INTEGER NOT NULL,
  name          TEXT NOT NULL,
  relation      TEXT,
  emoji         TEXT,
  notes         TEXT,
  speaker_emb   TEXT,
  created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_person ON alaya_persons(owner_ai_id, name);

-- ══════════ 形象照 ══════════
CREATE TABLE IF NOT EXISTS alaya_faces (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_ai_id   INTEGER NOT NULL,
  path          TEXT NOT NULL,
  descr         TEXT,
  created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_faces_owner ON alaya_faces(owner_ai_id);

-- ══════════ 忆点星光：记忆星系 ══════════
CREATE TABLE IF NOT EXISTS alaya_galaxy_invites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token         TEXT NOT NULL UNIQUE,
  inviter_user_id INTEGER NOT NULL,
  inviter_ai_id INTEGER NOT NULL,
  person_id     INTEGER NOT NULL DEFAULT 0,
  relation_type TEXT NOT NULL DEFAULT '',
  address_term  TEXT NOT NULL DEFAULT '',
  self_term     TEXT NOT NULL DEFAULT '',
  life_stage    TEXT,
  invitee_user_id INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL DEFAULT 'pending',
  v3_event_id   INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT DEFAULT (datetime('now','localtime')),
  accepted_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_invite_ai_person ON alaya_galaxy_invites(inviter_ai_id, person_id);

CREATE TABLE IF NOT EXISTS alaya_galaxy_stars (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_ai_id   INTEGER NOT NULL,
  person_id     INTEGER NOT NULL DEFAULT 0,
  episode_id    TEXT NOT NULL DEFAULT '',
  source        TEXT NOT NULL DEFAULT 'auto',
  lit           INTEGER NOT NULL DEFAULT 0,
  lit_by_ai_id  INTEGER NOT NULL DEFAULT 0,
  lit_at        TEXT,
  created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_star_episode ON alaya_galaxy_stars(owner_ai_id, episode_id);
CREATE INDEX IF NOT EXISTS idx_star_owner_person ON alaya_galaxy_stars(owner_ai_id, person_id);

CREATE TABLE IF NOT EXISTS alaya_galaxy_forwards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_ai_id   INTEGER NOT NULL,
  node_id       INTEGER NOT NULL DEFAULT 0,
  node_name     TEXT NOT NULL DEFAULT '',
  from_user_id  INTEGER NOT NULL,
  from_name     TEXT NOT NULL DEFAULT '',
  to_name       TEXT NOT NULL DEFAULT '',
  message       TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ══════════ 末那识心智涌现中枢（温态快照）══════════
CREATE TABLE IF NOT EXISTS alaya_manas_state (
  owner_ai_id   INTEGER PRIMARY KEY,
  tick          INTEGER NOT NULL DEFAULT 0,
  dominant      TEXT,
  dominant_activation REAL NOT NULL DEFAULT 0.0,
  layer         TEXT NOT NULL DEFAULT 'sub',
  silence_ticks INTEGER NOT NULL DEFAULT 0,
  updated_at    TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS alaya_manas_trace (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_ai_id   INTEGER NOT NULL,
  tick          INTEGER NOT NULL,
  layer         TEXT NOT NULL,
  activation    REAL NOT NULL DEFAULT 0.0,
  dominant      TEXT,
  created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_manas_trace ON alaya_manas_trace(owner_ai_id, tick);

-- ══════════ 用户主本体 ══════════
CREATE TABLE IF NOT EXISTS alaya_user_main (
  user_id       INTEGER PRIMARY KEY,
  main_ai_id    INTEGER NOT NULL,
  updated_at    TEXT
);

-- ══════════ ON UPDATE CURRENT_TIMESTAMP 的等价实现 ══════════
CREATE TRIGGER IF NOT EXISTS trg_self_model_updated
AFTER UPDATE ON alaya_self_model
FOR EACH ROW BEGIN
  UPDATE alaya_self_model SET updated_at=datetime('now','localtime') WHERE id=NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_manas_state_updated
AFTER UPDATE ON alaya_manas_state
FOR EACH ROW BEGIN
  UPDATE alaya_manas_state SET updated_at=datetime('now','localtime') WHERE owner_ai_id=NEW.owner_ai_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_voices_updated
AFTER UPDATE ON xd_voices
FOR EACH ROW BEGIN
  UPDATE xd_voices SET updated_at=datetime('now','localtime') WHERE id=NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_tones_updated
AFTER UPDATE ON me_c_tones
FOR EACH ROW BEGIN
  UPDATE me_c_tones SET updated_at=datetime('now','localtime') WHERE id=NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_speaker_updated
AFTER UPDATE ON xd_speaker
FOR EACH ROW BEGIN
  UPDATE xd_speaker SET updated_at=datetime('now','localtime') WHERE id=NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_ais_updated
AFTER UPDATE ON xd_ais
FOR EACH ROW BEGIN
  UPDATE xd_ais SET updated_at=datetime('now','localtime') WHERE id=NEW.id;
END;
