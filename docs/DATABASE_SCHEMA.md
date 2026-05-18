# DATABASE_SCHEMA.md
# Veritabanı Şema Spesifikasyonu

**Veritabanı:** PostgreSQL 15+  
**Karakter Seti:** UTF-8 (Türkçe karakter tam desteği için zorunlu)  
**Saat Dilimi:** Tüm zaman damgaları UTC'de saklanır (`TIMESTAMPTZ`)

---

## 1. Şema Genel Bakış

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│     workers     │     │      jobs        │     │  job_events     │
│─────────────────│     │──────────────────│     │─────────────────│
│ id (PK)         │◄────┤ worker_id (FK)   │◄────┤ job_id (FK)     │
│ hostname        │     │ id (PK)          │     │ worker_id (FK)  │
│ mac_address     │     │ input_path       │     │ event_type      │
│ ip_address      │     │ status           │     │ details (JSONB) │
│ status          │     │ priority         │     │ created_at      │
│ last_heartbeat  │     │ retry_count      │     └─────────────────┘
│ current_job_id  │     │ output_srt_path  │
│ ...             │     │ ...              │     ┌─────────────────┐
└─────────────────┘     └──────────────────┘     │ worker_metrics  │
         │                                        │─────────────────│
         └───────────────────────────────────────►│ worker_id (FK)  │
                                                  │ cpu_percent     │
┌─────────────────┐     ┌──────────────────┐     │ memory_percent  │
│ input_directories│    │ system_settings  │     │ gpu_percent     │
│─────────────────│     │──────────────────│     │ recorded_at     │
│ id (PK)         │     │ key (PK)         │     └─────────────────┘
│ path            │     │ value (JSONB)    │
│ output_path     │     │ description      │
│ is_active       │     │ updated_at       │
│ watch_recursively│    └──────────────────┘
└─────────────────┘
```

---

## 2. Uzantı Kurulumu

```sql
-- UUID oluşturma için (PK'larda gen_random_uuid() kullanmak üzere)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Dizi tabanlı arama performansı için (opsiyonel, tam metin arama için)
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
```

---

## 3. Tablo Tanımları

### 3.1 `workers` — İşçi Düğümleri

İşçi kaydı, yetenekleri, bağlantı bilgileri ve toplam istatistikler.

```sql
CREATE TABLE workers (
    -- Kimlik
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    hostname        TEXT        NOT NULL,
    mac_address     TEXT        NOT NULL UNIQUE,  -- Ağ arayüzü MAC; kalıcı kimlik
    ip_address      TEXT        NOT NULL,
    api_port        INTEGER     NOT NULL DEFAULT 8081,

    -- Durum
    -- 'online' | 'idle' | 'busy' | 'paused' | 'offline' | 'error'
    status          VARCHAR(20) NOT NULL DEFAULT 'offline',

    -- Donanım Yetenekleri (kayıt sırasında doldurulur)
    cpu_model       TEXT,                         -- örn: "Apple M3 Max"
    cpu_cores       INTEGER,
    memory_total_gb NUMERIC(6,2),
    gpu_model       TEXT,                         -- örn: "Apple M3 Max 30-core GPU"
    whisper_backend TEXT        NOT NULL DEFAULT 'mlx-whisper',
    worker_version  TEXT,                         -- İşçi paket sürümü

    -- Kalp Atışı
    last_heartbeat  TIMESTAMPTZ,
    heartbeat_interval_seconds INTEGER NOT NULL DEFAULT 30,

    -- Mevcut İş (denormalize — sorgulama kolaylığı için)
    current_job_id  UUID        REFERENCES jobs(id) ON DELETE SET NULL,

    -- Yaşam Boyu Toplamlar (kalp atışlarından artımlı güncellenir)
    jobs_completed           INTEGER     NOT NULL DEFAULT 0,
    jobs_failed              INTEGER     NOT NULL DEFAULT 0,
    total_audio_seconds      NUMERIC(12,2) NOT NULL DEFAULT 0,
    total_processing_seconds NUMERIC(12,2) NOT NULL DEFAULT 0,
    -- RTF = işleme süresi / ses süresi; <1.0 gerçek zamandan daha hızlı demek
    average_rtf              NUMERIC(6,4),

    -- Zaman Damgaları
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- İndeksler
CREATE INDEX idx_workers_status       ON workers(status);
CREATE INDEX idx_workers_mac_address  ON workers(mac_address);
CREATE INDEX idx_workers_last_heartbeat ON workers(last_heartbeat)
    WHERE status != 'offline';
```

**Durum Geçiş Kuralları:**

| Kaynak Durum | Hedef Durum | Tetikleyici |
|---|---|---|
| `offline` | `online` | İlk kayıt / yeniden bağlanma |
| `online` | `idle` | Kayıt başarılı |
| `idle` | `busy` | İş atandı |
| `busy` | `idle` | İş tamamlandı / başarısız |
| `busy` | `paused` | Dashboard'dan duraklat komutu |
| `paused` | `busy` | Dashboard'dan devam komutu |
| herhangi | `offline` | Kalp atışı zaman aşımı (90s) |
| herhangi | `error` | Kritik hata raporlandı |

---

### 3.2 `jobs` — İş Kayıtları

Her MP3 dosyası için bir satır. Tüm işlem yaşam döngüsünü izler.

```sql
CREATE TABLE jobs (
    -- Kimlik
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Dosya Bilgileri
    -- Koordinatör dosya köküne göre göreli yol, örn: "Proje_A/toplanti.mp3"
    input_path          TEXT        NOT NULL,
    original_filename   TEXT        NOT NULL,
    -- Göreli klasör yolu, çıktı hiyerarşisi korunması için, örn: "Proje_A"
    relative_folder     TEXT        NOT NULL DEFAULT '',
    file_size_bytes     BIGINT,
    -- Yineleme tespiti ve yeniden işleme kontrolü için MD5
    file_hash           CHAR(32),

    -- Durum
    -- 'pending' | 'assigned' | 'processing' | 'paused' | 'completed' | 'failed' | 'cancelled'
    status              VARCHAR(20) NOT NULL DEFAULT 'pending',

    -- Atama
    worker_id           UUID        REFERENCES workers(id) ON DELETE SET NULL,

    -- Öncelik (yüksek = önce işlenir)
    priority            INTEGER     NOT NULL DEFAULT 0,

    -- Yeniden Deneme
    retry_count         INTEGER     NOT NULL DEFAULT 0,
    max_retries         INTEGER     NOT NULL DEFAULT 3,
    last_error          TEXT,                       -- Son hata mesajı
    next_retry_after    TIMESTAMPTZ,                -- Geciktirilmiş yeniden deneme için

    -- İlerleme (işleme sırasında canlı güncellenir)
    progress_percent    NUMERIC(5,2),

    -- Zaman Damgaları
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    assigned_at         TIMESTAMPTZ,
    started_at          TIMESTAMPTZ,
    paused_at           TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,

    -- Sonuçlar (tamamlandıktan sonra doldurulur)
    -- Koordinatör çıktı köküne göre göreli yol
    output_srt_path     TEXT,
    output_json_path    TEXT,
    audio_duration_seconds  NUMERIC(10,2),
    processing_time_seconds NUMERIC(10,2),
    -- Gerçek Zaman Faktörü: processing_time / audio_duration
    rtf                 NUMERIC(6,4),

    -- Kısıtlamalar
    CONSTRAINT jobs_status_valid CHECK (status IN (
        'pending', 'assigned', 'processing', 'paused',
        'completed', 'failed', 'cancelled'
    )),
    CONSTRAINT jobs_retry_count_valid CHECK (retry_count >= 0),
    CONSTRAINT jobs_priority_range CHECK (priority BETWEEN -100 AND 100),
    CONSTRAINT jobs_progress_range CHECK (
        progress_percent IS NULL OR
        progress_percent BETWEEN 0 AND 100
    )
);

-- İş Kuyruğu İndeksi — Sık kullanılan: "Sonraki bekleyen işi getir"
-- (Yüksek öncelik, sonra en erken oluşturma tarihi; yalnızca bekleyen işler)
CREATE INDEX idx_jobs_queue
    ON jobs(priority DESC, created_at ASC)
    WHERE status = 'pending' AND (next_retry_after IS NULL OR next_retry_after <= NOW());

-- Durum + işçi — "İşçimin tüm aktif işleri" sorgusu için
CREATE INDEX idx_jobs_worker_status   ON jobs(worker_id, status)
    WHERE status IN ('assigned', 'processing', 'paused');

-- Durum izleme — Dashboard sayaçları için
CREATE INDEX idx_jobs_status           ON jobs(status);

-- Dosya karması — Yineleme tespiti için
CREATE INDEX idx_jobs_file_hash        ON jobs(file_hash)
    WHERE file_hash IS NOT NULL;

-- Tarih bazlı sorgular — Dashboard istatistikleri için
CREATE INDEX idx_jobs_created_at       ON jobs(created_at DESC);
CREATE INDEX idx_jobs_completed_at     ON jobs(completed_at DESC)
    WHERE status = 'completed';

-- Yeniden deneme — Hazır bekleyen işler için
CREATE INDEX idx_jobs_next_retry       ON jobs(next_retry_after)
    WHERE status = 'pending' AND next_retry_after IS NOT NULL;
```

---

### 3.3 `job_events` — İş Olay Günlüğü

İş başına değişmez olay akışı; denetim izi ve hata ayıklama için kullanılır.

```sql
CREATE TABLE job_events (
    id          BIGSERIAL   PRIMARY KEY,
    job_id      UUID        NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    worker_id   UUID        REFERENCES workers(id) ON DELETE SET NULL,

    -- 'created' | 'queued' | 'assigned' | 'download_started' | 'download_complete'
    -- | 'processing_started' | 'progress' | 'paused' | 'resumed'
    -- | 'upload_started' | 'completed' | 'failed' | 'cancelled' | 'retried'
    event_type  VARCHAR(50) NOT NULL,

    -- Olaya özgü ek veri (serbest biçim JSONB)
    -- Örnekler:
    --   'progress': {"percent": 42.5, "elapsed_seconds": 12.3}
    --   'failed':   {"error": "OOM", "exit_code": -9, "attempt": 2}
    --   'completed':{"audio_seconds": 1234.5, "processing_seconds": 456.7, "rtf": 0.37}
    details     JSONB,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- İş olaylarını kronolojik sırayla getirme — sık kullanılır
CREATE INDEX idx_job_events_job_id      ON job_events(job_id, created_at ASC);
-- İşçi başına son N olay — debug için
CREATE INDEX idx_job_events_worker_id   ON job_events(worker_id, created_at DESC)
    WHERE worker_id IS NOT NULL;
-- 'progress' olaylarını filtrele — dashboard zaman çizelgesi için dışlanabilir
CREATE INDEX idx_job_events_type        ON job_events(event_type);
```

**Önemli Not:** `job_events` kayıt silme işlemi yapılmamalıdır. Uzun dönem depolama için partition veya arşivleme stratejisi uygulanabilir (IMPLEMENTATION_PLAN'da belirtilmiştir).

---

### 3.4 `worker_metrics` — İşçi Zaman Serisi Metrikleri

Her kalp atışında işçi metrikleri kaydedilir. Dashboard grafiklerini besler.

```sql
CREATE TABLE worker_metrics (
    id                  BIGSERIAL   PRIMARY KEY,
    worker_id           UUID        NOT NULL REFERENCES workers(id) ON DELETE CASCADE,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Sistem Metrikleri
    cpu_percent         NUMERIC(5,2),
    memory_used_gb      NUMERIC(6,2),
    memory_total_gb     NUMERIC(6,2),
    memory_percent      NUMERIC(5,2),

    -- GPU Metrikleri (Apple Silicon — psutil Metal API'sinden)
    gpu_percent         NUMERIC(5,2),
    gpu_memory_used_gb  NUMERIC(6,2),

    -- İş Bağlamı (o anda işleniyorsa)
    current_job_id      UUID        REFERENCES jobs(id) ON DELETE SET NULL,
    job_progress_percent NUMERIC(5,2)
);

-- Zaman serisi sorgusu: belirli işçi için son N satır
CREATE INDEX idx_worker_metrics_worker_time
    ON worker_metrics(worker_id, recorded_at DESC);

-- Zaman aralığı sorguları — dashboard grafikleri için
CREATE INDEX idx_worker_metrics_recorded_at
    ON worker_metrics(recorded_at DESC);

-- Veri Saklama: 7 günden eski metrikler otomatik silinir
-- (cron görevi veya pg_partman ile yönetilir)
-- CREATE RULE worker_metrics_retention AS
--   DELETE FROM worker_metrics WHERE recorded_at < NOW() - INTERVAL '7 days';
```

**Veri Saklama Politikası:**
- İşçi metrikleri: 7 gün saklama (yapılandırılabilir)
- İş olayları: Sonsuza kadar saklanır (ya da 90 gün arşivleme ile)
- `jobs` ve `workers`: Sonsuza kadar saklanır

---

### 3.5 `input_directories` — İzlenen Giriş Dizinleri

Hangi dizinlerin MP3 için izlendiğini tanımlar.

```sql
CREATE TABLE input_directories (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    path                TEXT        NOT NULL UNIQUE,  -- Mutlak yol, örn: "/Volumes/Data/input"
    output_path         TEXT        NOT NULL,         -- Karşılık gelen çıktı dizini
    is_active           BOOLEAN     NOT NULL DEFAULT true,
    watch_recursively   BOOLEAN     NOT NULL DEFAULT true,
    -- Bu dizinin dosyaları için varsayılan öncelik
    default_priority    INTEGER     NOT NULL DEFAULT 0,
    -- Açıklama etiketi
    label               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT input_directories_path_nonempty CHECK (length(path) > 0),
    CONSTRAINT input_directories_output_nonempty CHECK (length(output_path) > 0)
);

CREATE INDEX idx_input_dirs_active ON input_directories(is_active)
    WHERE is_active = true;
```

---

### 3.6 `system_settings` — Sistem Yapılandırması

Çalışma zamanı ayarları; yeniden başlatmaya gerek kalmadan güncellenebilir.

```sql
CREATE TABLE system_settings (
    key         TEXT    PRIMARY KEY,
    value       JSONB   NOT NULL,
    description TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Varsayılan Değerler
INSERT INTO system_settings (key, value, description) VALUES
('worker_heartbeat_timeout_seconds',  '90',     'Bu süre sonra kalp atışı gelmezse işçi offline sayılır'),
('max_retries_default',               '3',      'Her iş için varsayılan maksimum yeniden deneme sayısı'),
('retry_delay_seconds',               '[0, 60, 300]', 'Yeniden deneme gecikmelerinin JSON dizisi (saniye)'),
('worker_metrics_retention_days',     '7',      'İşçi metrik saklama süresi (gün)'),
('job_events_retention_days',         '90',     'İş olayı saklama süresi (gün); null = sonsuza kadar'),
('max_concurrent_jobs_per_worker',    '1',      'İşçi başına maksimum eşzamanlı iş (şimdilik her zaman 1)'),
('dashboard_refresh_interval_ms',    '5000',   'Dashboard WebSocket kalp atışı aralığı (ms)'),
('file_watcher_debounce_seconds',     '2',      'Yeni dosyalar algılamadan önce dosya istikrarı bekleme süresi'),
('whisper_model',                     '"mlx-community/whisper-medium-mlx"', 'Kullanılan Whisper model tanımlayıcısı'),
('whisper_language',                  '"tr"',   'Transkripsiyon dili kodu'),
('whisper_word_timestamps',           'true',   'Kelime düzeyinde zaman damgası etkinleştirme')
ON CONFLICT (key) DO NOTHING;
```

---

## 4. Tetikleyiciler ve Fonksiyonlar

### 4.1 `updated_at` Otomatik Güncelleme

```sql
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_workers_updated_at
    BEFORE UPDATE ON workers
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_jobs_updated_at
    BEFORE UPDATE ON jobs
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_input_directories_updated_at
    BEFORE UPDATE ON input_directories
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_system_settings_updated_at
    BEFORE UPDATE ON system_settings
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();
```

### 4.2 İş Olay Günlüğü Tetikleyicisi

İş durumu her değiştiğinde otomatik olay kaydı oluşturur.

```sql
CREATE OR REPLACE FUNCTION trigger_log_job_status_change()
RETURNS TRIGGER AS $$
BEGIN
    -- Durum değişmediyse olay kaydetme
    IF OLD.status = NEW.status THEN
        RETURN NEW;
    END IF;

    INSERT INTO job_events (job_id, worker_id, event_type, details)
    VALUES (
        NEW.id,
        NEW.worker_id,
        NEW.status,  -- Durum adı olay türü olarak kullanılır
        jsonb_build_object(
            'previous_status', OLD.status,
            'new_status',      NEW.status,
            'retry_count',     NEW.retry_count
        )
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER log_job_status_change
    AFTER UPDATE OF status ON jobs
    FOR EACH ROW EXECUTE FUNCTION trigger_log_job_status_change();
```

### 4.3 İşçi Çevrimdışı Kurtarma Fonksiyonu

Koordinatör başlangıcında çağrılır: çökmüş işçilere atanmış işleri yeniden kuyruğa alır.

```sql
CREATE OR REPLACE FUNCTION recover_stale_jobs()
RETURNS INTEGER AS $$
DECLARE
    recovered_count INTEGER;
BEGIN
    -- Çevrimdışı işçilere atanmış veya onlar tarafından işlenen tüm işleri
    -- 'pending' durumuna geri al
    WITH stale_jobs AS (
        UPDATE jobs j
        SET
            status       = 'pending',
            worker_id    = NULL,
            assigned_at  = NULL,
            started_at   = NULL,
            paused_at    = NULL,
            progress_percent = NULL,
            updated_at   = NOW()
        FROM workers w
        WHERE j.worker_id = w.id
          AND w.status IN ('offline', 'error')
          AND j.status IN ('assigned', 'processing', 'paused')
          AND j.retry_count < j.max_retries
        RETURNING j.id
    )
    SELECT COUNT(*) INTO recovered_count FROM stale_jobs;

    -- max_retries'ı aşanları 'failed' olarak işaretle
    UPDATE jobs j
    SET
        status     = 'failed',
        last_error = 'İşçi bağlantı kesilmesi nedeniyle maksimum yeniden deneme sayısına ulaşıldı',
        updated_at = NOW()
    FROM workers w
    WHERE j.worker_id = w.id
      AND w.status IN ('offline', 'error')
      AND j.status IN ('assigned', 'processing', 'paused')
      AND j.retry_count >= j.max_retries;

    RETURN recovered_count;
END;
$$ LANGUAGE plpgsql;
```

---

## 5. Görünümler

### 5.1 Dashboard Özet Görünümü

```sql
CREATE OR REPLACE VIEW v_dashboard_summary AS
SELECT
    -- İş Sayaçları
    COUNT(*) FILTER (WHERE status = 'pending')    AS jobs_pending,
    COUNT(*) FILTER (WHERE status = 'processing') AS jobs_processing,
    COUNT(*) FILTER (WHERE status = 'paused')     AS jobs_paused,
    COUNT(*) FILTER (WHERE status = 'completed')  AS jobs_completed,
    COUNT(*) FILTER (WHERE status = 'failed')     AS jobs_failed,
    COUNT(*) FILTER (WHERE status = 'cancelled')  AS jobs_cancelled,
    COUNT(*)                                       AS jobs_total,

    -- İşleme İstatistikleri
    ROUND(SUM(audio_duration_seconds) FILTER (WHERE status = 'completed') / 3600.0, 2)
        AS total_audio_hours_completed,
    ROUND(AVG(rtf) FILTER (WHERE status = 'completed' AND rtf IS NOT NULL), 4)
        AS avg_rtf,
    ROUND(AVG(processing_time_seconds) FILTER (WHERE status = 'completed'), 1)
        AS avg_processing_seconds,

    -- Son 24 Saat
    COUNT(*) FILTER (WHERE status = 'completed' AND completed_at > NOW() - INTERVAL '24h')
        AS jobs_completed_last_24h,
    ROUND(
        SUM(audio_duration_seconds) FILTER (
            WHERE status = 'completed' AND completed_at > NOW() - INTERVAL '24h'
        ) / 3600.0, 2
    ) AS audio_hours_last_24h

FROM jobs;
```

### 5.2 İşçi Durum Görünümü

```sql
CREATE OR REPLACE VIEW v_worker_status AS
SELECT
    w.id,
    w.hostname,
    w.ip_address,
    w.status,
    w.cpu_model,
    w.cpu_cores,
    w.memory_total_gb,
    w.gpu_model,
    w.last_heartbeat,
    EXTRACT(EPOCH FROM (NOW() - w.last_heartbeat))::INTEGER AS seconds_since_heartbeat,
    w.current_job_id,
    j.input_path                AS current_job_path,
    j.progress_percent          AS current_job_progress,
    w.jobs_completed,
    w.jobs_failed,
    ROUND(w.total_audio_seconds / 3600.0, 2) AS total_audio_hours,
    w.average_rtf,

    -- Anlık Metrik (son kalp atışından)
    wm.cpu_percent              AS last_cpu_percent,
    wm.memory_percent           AS last_memory_percent,
    wm.gpu_percent              AS last_gpu_percent

FROM workers w
LEFT JOIN jobs j     ON w.current_job_id = j.id
LEFT JOIN LATERAL (
    SELECT cpu_percent, memory_percent, gpu_percent
    FROM worker_metrics
    WHERE worker_id = w.id
    ORDER BY recorded_at DESC
    LIMIT 1
) wm ON true
ORDER BY w.status DESC, w.hostname;
```

### 5.3 İş Kuyruğu Görünümü

```sql
CREATE OR REPLACE VIEW v_job_queue AS
SELECT
    j.id,
    j.input_path,
    j.original_filename,
    j.relative_folder,
    j.status,
    j.priority,
    j.retry_count,
    j.max_retries,
    j.progress_percent,
    j.file_size_bytes,
    w.hostname               AS assigned_worker_hostname,
    j.created_at,
    j.assigned_at,
    j.started_at,
    j.completed_at,
    CASE
        WHEN j.status = 'processing' AND j.started_at IS NOT NULL
        THEN EXTRACT(EPOCH FROM (NOW() - j.started_at))::INTEGER
        ELSE NULL
    END                      AS elapsed_seconds,
    j.audio_duration_seconds,
    j.processing_time_seconds,
    j.rtf,
    j.last_error
FROM jobs j
LEFT JOIN workers w ON j.worker_id = w.id
ORDER BY
    CASE j.status
        WHEN 'processing' THEN 1
        WHEN 'paused'     THEN 2
        WHEN 'assigned'   THEN 3
        WHEN 'pending'    THEN 4
        WHEN 'failed'     THEN 5
        WHEN 'completed'  THEN 6
        WHEN 'cancelled'  THEN 7
        ELSE 8
    END,
    j.priority DESC,
    j.created_at ASC;
```

---

## 6. Veritabanı Bakım Prosedürleri

### 6.1 Eski Metriklerin Temizlenmesi

```sql
-- Bu fonksiyon koordinatörün bakım cron görevi tarafından günde bir kez çağrılır
CREATE OR REPLACE FUNCTION cleanup_old_metrics()
RETURNS void AS $$
DECLARE
    retention_days INTEGER;
BEGIN
    SELECT (value::TEXT)::INTEGER
    INTO retention_days
    FROM system_settings
    WHERE key = 'worker_metrics_retention_days';

    retention_days := COALESCE(retention_days, 7);

    DELETE FROM worker_metrics
    WHERE recorded_at < NOW() - (retention_days || ' days')::INTERVAL;

    -- Eski progress job_events temizle (hacim azaltma)
    DELETE FROM job_events
    WHERE event_type = 'progress'
      AND created_at < NOW() - INTERVAL '24 hours';
END;
$$ LANGUAGE plpgsql;
```

### 6.2 VACUUM ve Analiz Yapılandırması

PostgreSQL `postgresql.conf` önerileri:

```ini
# jobs ve job_events tablolarında çok sayıda UPDATE olduğu için agresif autovacuum
autovacuum_vacuum_scale_factor = 0.05  # Tablo boyutunun %5'i değiştiğinde
autovacuum_analyze_scale_factor = 0.02 # Tablo boyutunun %2'si değiştiğinde
autovacuum_vacuum_cost_delay = 2ms     # Daha hızlı vakumlama

# Checkpoint
checkpoint_timeout = 10min
checkpoint_completion_target = 0.9

# Bellek (Mac Studio 32GB için)
shared_buffers = 512MB
work_mem = 64MB
maintenance_work_mem = 256MB
effective_cache_size = 8GB
```

---

## 7. Alembic Migrasyon Yapısı

```
migrations/
├── env.py
├── script.py.mako
└── versions/
    ├── 0001_initial_schema.py        # Tüm temel tablolar
    ├── 0002_add_triggers.py          # updated_at tetikleyicileri
    ├── 0003_add_views.py             # v_dashboard_summary, v_worker_status, v_job_queue
    ├── 0004_seed_system_settings.py  # Varsayılan ayarlar
    └── 0005_add_cleanup_functions.py # cleanup_old_metrics(), recover_stale_jobs()
```

Her migrasyon `upgrade()` ve `downgrade()` fonksiyonlarını içerir. Migrasyon süreci tam transaksiyoneldir.

---

## 8. Veri Türü Kararları

| Sütun | Tür | Gerekçe |
|---|---|---|
| PK'lar | `UUID` | Dağıtık sistemde çakışmasız kimlik; DB katmanında sıralı oluşturma |
| Zaman Damgaları | `TIMESTAMPTZ` | Saat dilimi farklılıklarından kaçınmak için UTC zorunlu |
| Ondalık Sayılar | `NUMERIC(p,s)` | Kayan nokta hatalarından kaçınmak için ses süresi ve RTF'de |
| Hata Mesajları | `TEXT` | Sınırsız uzunluk; Python izleme çıktısı uzun olabilir |
| Olay Detayları | `JSONB` | Şemasız esneklik; GIN indeksleme ile sorgulanabilir |
| Ayarlar | `JSONB` | Her türde değer (tamsayı, dizi, string) tek sütunda |
| Durum Alanları | `VARCHAR(20)` | Kısıtlı uzunluk; CHECK kısıtlamaları geçerli değerleri zorlar |

---

*Sonraki belge: [API_SPEC.md](API_SPEC.md)*
