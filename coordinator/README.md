# Transkripsiyon Kümesi — Koordinatör

Dağıtık Apple Silicon transkripsiyon kümesi için üretim kalitesinde FastAPI koordinatörü.

## Gereksinimler

- **Python:** 3.11+
- **PostgreSQL:** 15+ (Postgres.app önerilir)
- **macOS:** 14+ (Sonoma veya üzeri)

## Hızlı Başlangıç (Geliştirme)

```bash
cd coordinator/

# Ortamı kur (bir kez)
./scripts/setup_dev.sh

# Sanal ortamı etkinleştir
source .venv/bin/activate

# Geliştirme modunda başlat (sıcak yeniden yükleme + renkli loglar)
./scripts/start.sh --dev
```

API dokümantasyonu: http://localhost:8080/docs

## Dizin Yapısı

```
coordinator/
├── app/
│   ├── main.py              # FastAPI uygulaması, lifespan, WebSocket uç noktaları
│   ├── config.py            # Pydantic Settings (ortam değişkenleri)
│   ├── database.py          # Async SQLAlchemy motoru ve oturum fabrikası
│   ├── logging_config.py    # JSON yapılandırılmış günlük kurulumu
│   ├── models/              # SQLAlchemy ORM modelleri
│   │   ├── enums.py         # JobStatus, WorkerStatus, ErrorCategory
│   │   ├── job.py           # İş modeli
│   │   ├── worker.py        # İşçi modeli
│   │   ├── job_event.py     # İş olay günlüğü
│   │   ├── worker_metric.py # Zaman serisi işçi metrikleri
│   │   ├── input_directory.py
│   │   └── system_setting.py
│   ├── schemas/             # Pydantic v2 istek/yanıt modelleri
│   ├── api/v1/              # REST API uç noktaları
│   │   ├── jobs.py          # İş CRUD, duraklat/devam/iptal/yeniden dene
│   │   ├── workers.py       # İşçi yönetimi
│   │   ├── worker_internal.py # İşçi ajanı API'si (kayıt, kalp atışı, iş)
│   │   ├── files.py         # MP3 indirme, SRT/JSON çıktı indirme
│   │   └── system.py        # İstatistikler, ayarlar, dizin yönetimi
│   ├── websocket/
│   │   ├── manager.py       # WebSocket bağlantı kayıt defteri
│   │   └── events.py        # Tip güvenli olay tanımları
│   ├── services/
│   │   ├── job_queue.py     # Atomik iş talep etme, durum geçişleri
│   │   ├── worker_monitor.py# Kalp atışı izleme, stale iş kurtarma
│   │   ├── file_watcher.py  # Giriş dizini izleme (watchdog)
│   │   ├── mdns_announcer.py# mDNS servis duyurusu (Zeroconf)
│   │   └── maintenance.py   # Günlük temizlik görevi
│   └── core/
│       ├── exceptions.py    # Özel istisnalar ve HTTP fabrikaları
│       ├── dependencies.py  # FastAPI bağımlılıkları (DbSession, WsManager)
│       └── security.py      # Dosya yolu güvenlik yardımcıları
├── migrations/              # Alembic migrasyon sürümleri
│   └── versions/
│       ├── 0001_initial_schema.py
│       ├── 0002_triggers_and_functions.py
│       ├── 0003_views.py
│       └── 0004_seed_settings.py
├── scripts/
│   ├── setup_dev.sh         # Tek seferlik geliştirme ortamı kurulumu
│   └── start.sh             # Sunucu başlatma (--dev bayrağı desteklenir)
├── static/                  # React dashboard derleme çıktısı buraya gelir
├── .env.example             # Örnek ortam değişkenleri
├── requirements.txt         # Üretim bağımlılıkları
├── requirements-dev.txt     # Geliştirme bağımlılıkları
└── alembic.ini
```

## Ortam Değişkenleri

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://localhost/transcription_cluster` | PostgreSQL bağlantı URL'si |
| `COORDINATOR_HOST` | `0.0.0.0` | Dinleme adresi |
| `COORDINATOR_PORT` | `8080` | HTTP/WebSocket portu |
| `INPUT_BASE_DIR` | `/opt/transcription-data/input` | İzlenen giriş dizini kökü |
| `OUTPUT_BASE_DIR` | `/opt/transcription-data/output` | SRT/JSON çıktı kökü |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `JSON_LOGS` | `true` | JSON formatı (üretim için `true`) |
| `WORKER_HEARTBEAT_TIMEOUT_SECONDS` | `90` | Bu süre sonra işçi offline sayılır |
| `RECOVERY_GRACE_SECONDS` | `30` | Koordinatör yeniden başlatmasında yeniden bağlanma süresi |

## API Özeti

### Dashboard Uç Noktaları

| Uç Nokta | Açıklama |
|---|---|
| `GET  /api/v1/jobs` | İş listesi (filtreleme + sayfalama) |
| `POST /api/v1/jobs/{id}/pause` | İşi duraklat |
| `POST /api/v1/jobs/{id}/resume` | İşi devam ettir |
| `POST /api/v1/jobs/{id}/cancel` | İşi iptal et |
| `POST /api/v1/jobs/{id}/retry` | Başarısız işi yeniden dene |
| `GET  /api/v1/workers` | İşçi listesi |
| `GET  /api/v1/system/stats` | Sistem istatistikleri |
| `GET  /api/v1/system/settings` | Çalışma zamanı ayarları |

### İşçi İç Uç Noktaları

| Uç Nokta | Açıklama |
|---|---|
| `POST /api/v1/worker/register` | İşçi kaydı / yeniden bağlanma |
| `POST /api/v1/worker/heartbeat` | Kalp atışı + metrik gönderimi |
| `GET  /api/v1/worker/jobs/next` | Sonraki işi atomik olarak talep et |
| `POST /api/v1/worker/jobs/{id}/start` | İşi başlatıldı bildir |
| `POST /api/v1/worker/jobs/{id}/progress` | İlerleme güncelle |
| `POST /api/v1/worker/jobs/{id}/complete` | SRT + JSON yükle |
| `POST /api/v1/worker/jobs/{id}/fail` | Hata bildir |

### Dosya Uç Noktaları

| Uç Nokta | Açıklama |
|---|---|
| `GET /api/v1/files/{job_id}/download` | MP3 akışı (Range destekli) |
| `GET /api/v1/files/output/{job_id}/srt` | SRT çıktısını indir |
| `GET /api/v1/files/output/{job_id}/json` | JSON çıktısını indir |

### WebSocket

| Uç Nokta | Açıklama |
|---|---|
| `WS /ws/dashboard` | Dashboard gerçek zamanlı olaylar |
| `WS /ws/worker?worker_id={id}` | İşçi komut kanalı |

### Sağlık Uç Noktaları

| Uç Nokta | Açıklama |
|---|---|
| `GET /healthz` | Temel sağlık kontrolü |
| `GET /readyz` | Hazır olma durumu (grace period dahil) |

## Migrasyon Komutları

```bash
# Şemayı en son sürüme güncelle
alembic upgrade head

# Mevcut sürümü göster
alembic current

# Migrasyon geçmişini göster
alembic history

# Geri al (bir sürüm)
alembic downgrade -1
```

## Temel Tasarım Kararları

### Tek Uvicorn İşçisi (`--workers 1`)
WebSocket bağlantıları süreçe özgüdür. Birden fazla Uvicorn işçisi, farklı işçi süreçlere bağlı dashboard istemcilerinin gerçek zamanlı güncellemeleri alamamasına neden olur. Koordinatör CPU'ya bağlı değildir (gerçek transkripsiyon işçilerde); tek async süreç + uvloop 20+ işçiyi sorunsuz yönetir.

### `FOR UPDATE SKIP LOCKED` İş Kuyruğu
Birden fazla işçi aynı anda `GET /api/v1/worker/jobs/next` çağırdığında race condition yoktur. PostgreSQL satır düzeyinde kilitleme ile atomik talep garantilenir.

### Kısmi İndeks (`WHERE status = 'pending'`)
İş kuyruğu indeksi yalnızca bekleyen işleri kapsar. `NOW()` volatile fonksiyonu kısmi indeks koşulunda kullanılamaz (PostgreSQL IMMUTABLE gerektirir); `next_retry_after` filtresi sorgu çalışma zamanında uygulanır.

### Atomik Dosya Yazma (tmp → rename)
SRT ve JSON çıktıları önce `output.srt.tmp` olarak yazılır, ardından `os.rename()` ile final yola taşınır. Aynı dosya sistemi üzerinde bu işlem atomiktir; koordinatör çökmesi durumunda yarım dosya kalmaz.

### Grace Period Protokolü
Koordinatör yeniden başladığında 30 saniye bekler (varsayılan). Bu süre boyunca işçiler yeniden bağlanabilir ve `current_job_id` bildirerek işlerine devam edebilir. Grace period sonunda yalnızca hâlâ offline olan işçilerin işleri yeniden kuyruğa alınır.

### İlerleme Olayları Veritabanına Yazılmaz
`job_events` tablosu yalnızca durum geçişlerini kaydeder. 10 saniyede bir gelen ilerleme güncellemeleri yalnızca `jobs.progress_percent` sütununu günceller (tek satır UPDATE). 2 saatlik bir dosya için 720 gereksiz satır oluşturulması önlenir.

## Sorun Giderme

### PostgreSQL bağlantı hatası
```bash
# Postgres.app çalışıyor mu?
pg_isready -h localhost

# Veritabanı var mı?
psql -l | grep transcription_cluster

# Oluştur
createdb transcription_cluster
```

### Migrasyon hatası
```bash
# Mevcut durumu kontrol et
alembic current

# Gerekirse sıfırla (DİKKAT: veri kaybı!)
psql -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" transcription_cluster
alembic upgrade head
```

### mDNS çalışmıyor
macOS güvenlik duvarı mDNS çok noktaya yayını engelliyor olabilir. İşçiler manuel IP ile de bağlanabilir:
```bash
# İşçi config.env dosyasında:
COORDINATOR_HOST=192.168.1.101
```
