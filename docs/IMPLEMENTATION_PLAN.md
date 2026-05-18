# IMPLEMENTATION_PLAN.md
# Uygulama Planı

**Toplam Tahmini Süre:** 9 Hafta (Tek Geliştirici) / 5 Hafta (İki Geliştirici)  
**Metodoloji:** Fazlı geliştirme — her fazın sonunda çalışan bir sistem bulunur

---

## Genel Bakış

```
Faz 1: Temel Altyapı          (Hafta 1–2)   → Boş iskelet çalışıyor
Faz 2: Koordinatör Çekirdeği  (Hafta 2–3)   → İş kuyruğu ve dosya yönetimi
Faz 3: İşçi Ajanı             (Hafta 3–5)   → Gerçek transkripsiyon çalışıyor
Faz 4: Hata Toleransı         (Hafta 5–6)   → Üretim sağlamlığı
Faz 5: Dashboard              (Hafta 5–7)   → Tam izleme arayüzü
Faz 6: Paketleme              (Hafta 7–8)   → Çevrimdışı kurulum hazır
Faz 7: Test ve Güçlendirme    (Hafta 8–9)   → Üretim doğrulaması
```

---

## Faz 1: Temel Altyapı (Hafta 1–2)

### Amaç
Tüm bileşenlerin üzerine inşa edeceği temel yapıyı oluşturmak.

### Görevler

#### 1.1 Depo Yapısı

```
transcription-cluster/
├── coordinator/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py          ← FastAPI uygulama oluşturma
│   │   ├── config.py        ← Pydantic ayarlar
│   │   ├── database.py      ← SQLAlchemy async engine
│   │   ├── models/          ← SQLAlchemy ORM modelleri
│   │   │   ├── __init__.py
│   │   │   ├── job.py
│   │   │   ├── worker.py
│   │   │   ├── job_event.py
│   │   │   ├── worker_metric.py
│   │   │   ├── input_directory.py
│   │   │   └── system_setting.py
│   │   ├── schemas/         ← Pydantic istek/yanıt modelleri
│   │   │   ├── job.py
│   │   │   ├── worker.py
│   │   │   └── system.py
│   │   ├── api/
│   │   │   └── v1/
│   │   │       ├── __init__.py
│   │   │       ├── jobs.py
│   │   │       ├── workers.py
│   │   │       ├── worker_internal.py
│   │   │       ├── files.py
│   │   │       └── system.py
│   │   ├── services/
│   │   │   ├── job_queue.py
│   │   │   ├── worker_monitor.py
│   │   │   ├── file_watcher.py
│   │   │   └── mdns_announcer.py
│   │   └── websocket/
│   │       ├── manager.py
│   │       └── handlers.py
│   ├── migrations/
│   │   ├── env.py
│   │   └── versions/
│   ├── requirements.txt
│   └── alembic.ini
├── worker/
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── models.py        ← Veri sınıfları
│   │   ├── discovery.py
│   │   ├── coordinator_client.py
│   │   ├── job_runner.py
│   │   ├── transcriber.py
│   │   ├── srt_generator.py
│   │   ├── json_generator.py
│   │   └── metrics_collector.py
│   └── requirements.txt
├── dashboard/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── hooks/
│   │   ├── store/
│   │   ├── types/
│   │   ├── lib/
│   │   └── main.tsx
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   └── tsconfig.json
├── scripts/
│   ├── build_coordinator_package.sh
│   ├── build_worker_package.sh
│   ├── install/
│   │   ├── coordinator/install.sh
│   │   └── worker/install.sh
│   └── launchd/
│       ├── coordinator/com.transcription.coordinator.plist
│       └── worker/com.transcription.worker.plist
├── docs/                    ← Bu belgeler
├── tests/
│   ├── coordinator/
│   ├── worker/
│   └── integration/
├── VERSION
└── README.md
```

#### 1.2 PostgreSQL Şema Kurulumu

- [ ] Alembic yapılandırması (`alembic.ini`, `migrations/env.py`)
- [ ] Migrasyon 0001: Tüm tablo tanımları (DATABASE_SCHEMA.md'den)
- [ ] Migrasyon 0002: Tetikleyiciler (`updated_at`, olay günlüğü)
- [ ] Migrasyon 0003: Görünümler (`v_dashboard_summary`, `v_worker_status`, `v_job_queue`)
- [ ] Migrasyon 0004: Varsayılan `system_settings` tohumları
- [ ] Migrasyon 0005: Bakım fonksiyonları

**Doğrulama:** `alembic upgrade head` sıfırdan çalışıyor; tüm tablolar, indeksler, kısıtlamalar mevcut.

#### 1.3 FastAPI İskeleti

- [ ] `app/main.py`: Uygulama oluşturma, router montajı, yaşam döngüsü kancaları
- [ ] `app/config.py`: Pydantic ayarları (ortam değişkenleri + `.env` dosyası)
- [ ] `app/database.py`: Async SQLAlchemy motoru, oturum bağımlılığı
- [ ] SQLAlchemy ORM modelleri (tüm tablolar için)
- [ ] Temel Pydantic şemaları
- [ ] `GET /api/v1/system/stats` — veri döndürmeyen tek uç nokta (sağlık kontrolü)
- [ ] `GET /healthz` — yük dengeleyici sağlık uç noktası

**Doğrulama:** `uvicorn app.main:app` başlatılıyor; `/healthz` 200 döndürüyor.

#### 1.4 React Dashboard İskeleti

- [ ] Vite + React 18 + TypeScript kurulumu
- [ ] TailwindCSS v4 yapılandırması
- [ ] shadcn/ui başlatma
- [ ] Temel düzen: kenar çubuğu + içerik alanı
- [ ] Sayfa yönlendirmesi (React Router): `/`, `/isler`, `/isciler`, `/ayarlar`
- [ ] Karanlık/açık mod geçişi (Tailwind `dark:` sistemi)
- [ ] Vite proxy yapılandırması (`/api` → `localhost:8080`)

**Doğrulama:** `npm run dev` çalışıyor; tüm sayfalar iskelet içerikle yükleniyor; karanlık mod geçiyor.

---

## Faz 2: Koordinatör Çekirdeği (Hafta 2–3)

### Amaç
Dosyaları algılayan, işleri oluşturan ve işçi kaydını işleyen koordinatörü tamamlamak.

### Görevler

#### 2.1 İş Kuyruğu Yönetimi

- [ ] `POST /api/v1/worker/register` — işçi kaydı / güncellemesi
- [ ] `GET /api/v1/worker/jobs/next` — atomik iş talep etme (`FOR UPDATE SKIP LOCKED`)
- [ ] `POST /api/v1/worker/jobs/{id}/start` — iş başlangıç kaydı
- [ ] `POST /api/v1/worker/jobs/{id}/progress` — ilerleme güncelleme
- [ ] `POST /api/v1/worker/jobs/{id}/complete` — multipart yükleme + sonuç kaydetme
- [ ] `POST /api/v1/worker/jobs/{id}/fail` — yeniden deneme mantığı
- [ ] `POST /api/v1/worker/heartbeat` — kalp atışı + metrik kayıt

**Önemli Detay:** `jobs/next` uç noktasında PostgreSQL `FOR UPDATE SKIP LOCKED` kullanımı — race condition olmadan birden fazla işçi aynı anda istek yapabilir.

#### 2.2 Dosya İzleyici Servisi

- [ ] `app/services/file_watcher.py`: watchdog tabanlı MP3 dosyası izleme
- [ ] Giriş dizinleri veritabanından yükleniyor (`input_directories` tablosu)
- [ ] MD5 karması hesaplama ve yineleme tespiti
- [ ] `relative_folder` hesaplama (girişe göre göreli yol)
- [ ] Yeni dosya algılandığında `jobs` tablosuna INSERT
- [ ] 2 saniyelik debounce (kısmi yazma tamamlanana kadar bekleme)

**Doğrulama:** İzlenen dizine MP3 kopyala → `jobs` tablosunda satır oluşturuluyor.

#### 2.3 Dosya Sunucusu

- [ ] `GET /api/v1/files/{job_id}/download` — akışlı MP3 sunma, Range isteği desteği
- [ ] Dosya yolu güvenliği (kök dizin dışına çıkma engeli)
- [ ] `POST /api/v1/worker/jobs/{id}/complete` içinde SRT + JSON kaydetme
- [ ] Çıktı klasör hiyerarşisi oluşturma (`os.makedirs(exist_ok=True)`)

#### 2.4 İşçi Monitörü

- [ ] `app/services/worker_monitor.py`: arka plan görev (asyncio)
- [ ] Her 15 saniyede `last_heartbeat > 90s` kontrolü
- [ ] Zaman aşımı olan işçiyi `offline` olarak işaretle
- [ ] Etkilenen işleri `pending` durumuna geri al (`recover_stale_jobs()` çağrısı)
- [ ] WebSocket üzerinden dashboard bildirimi

#### 2.5 mDNS Duyurusu

- [ ] `app/services/mdns_announcer.py`: `_transcription._tcp.local.` kaydı
- [ ] FastAPI başlangıcında başlatma, kapanışta durdurma
- [ ] Hizmet özellikleri: versiyon, koordinatör adı, port

**Doğrulama:** Başka bir Mac'ten `dns-sd -B _transcription._tcp local` komutunu çalıştır → koordinatör görünüyor.

#### 2.6 Dashboard API Uç Noktaları

- [ ] `GET /api/v1/jobs` — sayfalandırma, filtreleme, sıralama
- [ ] `GET /api/v1/jobs/{id}` — olay geçmişiyle birlikte
- [ ] `GET /api/v1/workers` — v_worker_status görünümü
- [ ] `GET /api/v1/system/stats` — özet istatistikler
- [ ] `POST /api/v1/jobs/{id}/cancel`, `/pause`, `/resume`, `/retry`
- [ ] `GET /api/v1/system/settings` + `PUT`
- [ ] Giriş dizini CRUD uç noktaları

---

## Faz 3: İşçi Ajanı (Hafta 3–5)

### Amaç
Gerçek transkripsiyon yapan, koordinatörle tam entegre işçiyi tamamlamak.

### Görevler

#### 3.1 mDNS Keşfi

- [ ] `agent/discovery.py`: `_transcription._tcp.local.` dinleyicisi
- [ ] Keşif sonucunu `~/.transcription-worker/coordinator.json`'a önbellekleme
- [ ] 60 saniyelik zaman aşımı; önbellekten yedek
- [ ] `COORDINATOR_HOST` ortam değişkeni ile manuel geçersiz kılma

**Doğrulama:** İşçiyi başlat, koordinatör otomatik keşfediliyor.

#### 3.2 İşçi Ajanı Ana Döngüsü

- [ ] `agent/main.py`: asyncio olay döngüsü, graceful shutdown
- [ ] `agent/config.py`: Pydantic ayarlar, ortam + `.env` yükleme
- [ ] Kalp atışı görevi (bağımsız asyncio görevi)
- [ ] İş döngüsü görevi (bağımsız asyncio görevi)
- [ ] WebSocket bağlantı yönetimi
- [ ] SIGTERM yakalanıyor → graceful shutdown tetikleniyor
- [ ] Yeniden bağlanma döngüsü (geri çekilme + jitter)

**Doğrulama:** İşçiyi başlat → koordinatörde kayıt görünüyor; kalp atışları geliyor.

#### 3.3 mlx-whisper Entegrasyonu

- [ ] `agent/transcriber.py`:
  - [ ] Alt süreç tabanlı mlx-whisper çalıştırma (SIGSTOP/SIGCONT için)
  - [ ] `asyncio.create_subprocess_exec` ile Python alt süreci
  - [ ] stdout'tan JSON sonucu parse etme
  - [ ] 0.5 saniyelik döngüde komut kuyruğu kontrol etme
  - [ ] PAUSE komutunu işleme: `os.kill(pid, signal.SIGSTOP)`
  - [ ] RESUME komutunu işleme: `os.kill(pid, signal.SIGCONT)`
  - [ ] CANCEL komutunu işleme: `proc.kill()`
  - [ ] Tahmini ilerleme hesaplama (RTF tabanlı)
  - [ ] Ses süresi tespiti (`ffprobe` veya `mutagen` kütüphanesi)

**Doğrulama:** Tek bir MP3 dosyasını yükle → SRT ve JSON çıktıları üretiliyor; doğrulama: Türkçe içerik doğru transkrip ediliyor.

#### 3.4 SRT ve JSON Üretimi

- [ ] `agent/srt_generator.py`: segment listesi → `.srt` dosyası (UTF-8)
- [ ] `agent/json_generator.py`: segment listesi + meta veri → `.json` dosyası
- [ ] Zaman damgası biçimlendirmesi: `HH:MM:SS,mmm`
- [ ] UTF-8 çıktı: `ensure_ascii=False` (Türkçe karakterler için zorunlu)

#### 3.5 Sistem Metrikleri Toplama

- [ ] `agent/metrics_collector.py`: psutil tabanlı CPU/bellek
- [ ] `ioreg` aracılığıyla Apple GPU kullanımı (sudo gerektirmez)
- [ ] Hata durumunda None döndürme (GPU metrikleri isteğe bağlı)

#### 3.6 Uçtan Uca Entegrasyon Testi

- [ ] Koordinatörü başlat → İşçiyi başlat
- [ ] 5 örnek MP3 dosyası işle (farklı süre ve kalitede)
- [ ] Klasör hiyerarşisi doğrulama
- [ ] Yeniden bağlanma testi: işçiyi işleme sırasında kapat → yeniden başlat
- [ ] Türkçe transkripsiyon kalitesi değerlendirmesi (5+ örnek)

---

## Faz 4: Hata Toleransı (Hafta 5–6)

### Amaç
Tüm hata senaryolarını sistematik olarak test etmek ve sağlamlaştırmak.

### Görevler

#### 4.1 Kapsamlı Yeniden Deneme Mantığı

- [ ] Yeniden deneme gecikmesi hesaplama (yapılandırılabilir dizi)
- [ ] `next_retry_after` veritabanında kaydetme
- [ ] İş alma sorgusunda `next_retry_after <= NOW()` filtresi
- [ ] Koordinatör başlangıç kurtarma rutini (`recover_stale_jobs()` çağrısı)

#### 4.2 Koordinatör Yeniden Başlatma Testi

- [ ] Test: Koordinatörü işlemler sırasında durdur
- [ ] Test: Koordinatörü yeniden başlat → işler kurtarılıyor mu?
- [ ] Test: İşçiler yeniden bağlanıyor mu?
- [ ] Test: Koordinatör durum tutarlılığı (tüm `assigned`/`processing` işler `pending` oluyor)

#### 4.3 İşçi Hata Senaryoları

- [ ] Test: İşleme sırasında işçiyi SIGKILL ile öldür
- [ ] Test: 90 saniye ağ bağlantısını kes → işçiyi yeniden bağla
- [ ] Test: İşçi sürecini restart launchd üzerinden
- [ ] Test: OOM simülasyonu (çok büyük bir dosya)
- [ ] Test: Bozuk MP3 dosyası (mlx-whisper başarısız oluyor)

#### 4.4 Duraklatma / Devam Testi

- [ ] Test: Dashboard'dan işi duraklat → işçi SIGSTOP alıyor
- [ ] Test: CPU kullanımı sıfıra iniyor (doğrulama)
- [ ] Test: Dashboard'dan devam et → kaldığı yerden devam ediyor
- [ ] Test: Duraklatılmış durumda yeniden bağlanma
- [ ] Test: İşçi kapat → duraklat → yeniden başlat (yeniden kuyruk)

#### 4.5 Disk Dolu Senaryosu

- [ ] Yükleme sırasında disk hatası yakalanıyor
- [ ] Dashboard'a uyarı gönderiliyor
- [ ] İş `failed` olarak işaretleniyor, retry uygulanıyor

#### 4.6 Eş Zamanlılık Testi

- [ ] 3+ işçiyle aynı anda iş talep etme (race condition yok)
- [ ] Aynı dosyayı birden fazla kez kuyruğa eklemeye çalış (yineleme tespiti)

---

## Faz 5: Dashboard (Hafta 5–7)

### Amaç
Tam işlevsel Türkçe arayüz (Faz 3 ile paralel geliştirilebilir).

### Görevler

#### 5.1 Altyapı Katmanı

- [ ] TanStack Query yapılandırması (önbellek, yeniden deneme, arka plan yenileme)
- [ ] Zustand küresel durum deposu
- [ ] WebSocket kancası (`useDashboardWebSocket`)
- [ ] API istemci yardımcıları (`src/lib/api.ts`)
- [ ] Toast bildirimleri (`src/components/Toaster.tsx`)
- [ ] Bağlantı durumu göstergesi bileşeni
- [ ] Türkçe tarih/saat biçimlendirme (`date-fns/locale/tr`)

#### 5.2 Ana Sayfa

- [ ] 4 özet metrik kartı (TanStack Query + WS güncelleme)
- [ ] Aktif işçi mini kartları (canlı metrik çubukları + iş ilerlemesi)
- [ ] İş durumu dağılım grafiği (Recharts)
- [ ] Saatlik verim çizgi grafiği (son 24 saat)
- [ ] Son tamamlanan işler listesi
- [ ] Sistem uyarıları bölümü

#### 5.3 İş Listesi Sayfası

- [ ] Arama + filtreleme arayüzü
- [ ] Sanallaştırılmış tablo (`react-virtual` veya `TanStack Table`)
- [ ] Klasöre göre gruplama görünümü
- [ ] Satır başına eylemler (duraklat/devam/iptal/yeniden dene)
- [ ] Toplu seçim ve toplu eylemler
- [ ] Sayfalandırma
- [ ] Tamamlanan işler için SRT/JSON indirme bağlantıları
- [ ] WS iş güncelleme entegrasyonu (anlık durum/ilerleme değişimleri)

#### 5.4 İş Detay Sayfası

- [ ] Tam bilgi ızgarası
- [ ] Canlı ilerleme çubuğu (işleme sırasında)
- [ ] Tahmini bitiş süresi
- [ ] Olay zaman çizelgesi bileşeni
- [ ] Hata detay kutusu (başarısız işler)
- [ ] İndir butonları (tamamlananlar)

#### 5.5 İşçi Listesi ve Detay Sayfaları

- [ ] İşçi kart ızgarası (canlı metrikler)
- [ ] İşçi detay sayfası: canlı grafik (son 1 saat CPU/RAM/GPU)
- [ ] Geçmiş iş tablosu
- [ ] Performans istatistikleri özeti
- [ ] İşçi duraklat/devam butonları

#### 5.6 Ayarlar Sayfası

- [ ] Giriş dizini listesi (ekle/düzenle/sil/etkinleştir)
- [ ] Sistem ayarları formu (kalp atışı zaman aşımı, max retry vb.)
- [ ] "Dizini Şimdi Tara" butonu
- [ ] Sistem bilgisi salt okunur bölümü

#### 5.7 Genel Bileşenler

- [ ] Durum rozeti bileşeni (durum → renk/metin)
- [ ] İlerleme çubuğu animasyonu
- [ ] Kenar çubuğu canlı sayaçları (WS entegrasyonu)
- [ ] Klavye kısayolları (`useHotkeys`)
- [ ] Boş durum tasarımları
- [ ] Hata sınırları (`ErrorBoundary`)
- [ ] Yükleme iskeleti ekranları

---

## Faz 6: Paketleme (Hafta 7–8)

### Amaç
Herhangi bir Mac Studio'ya internet erişimi olmadan kurulabilen paketler üretmek.

### Görevler

#### 6.1 Koordinatör Paketi

- [ ] `scripts/build_coordinator_package.sh` betiği
- [ ] Tüm Python wheel'larını `--platform macosx_14_0_arm64` ile indirme
- [ ] React dashboard üretim derlemesi dahil etme
- [ ] `install.sh` betiğini test etme (temiz macOS VM'de)
- [ ] launchd plist şablonları ve `sed` değişkeni
- [ ] `uninstall.sh` betiği
- [ ] Bütünlük doğrulaması betiği (`verify_coordinator_install.sh`)

#### 6.2 İşçi Paketi

- [ ] `scripts/build_worker_package.sh` betiği
- [ ] Whisper Medium MLX modeli önceden indirme ve paketleme
- [ ] Model bütünlük doğrulaması (SHA256 karma kontrolü)
- [ ] Model `local_dir_use_symlinks=False` ile offline kopyalama
- [ ] `install.sh` betiğini test etme (temiz macOS VM'de)
- [ ] Bütünlük doğrulaması betiği (model yükleme testi dahil)
- [ ] `README_ISCI_KURULUM.md`: adım adım kurulum kılavuzu

#### 6.3 Güncelleme Mekanizması

- [ ] Model olmadan hafif işçi güncelleme paketi (`worker-update-v*.tar.gz`)
- [ ] Koordinatör güncelleme betiği (PostgreSQL verisi dokunulmaz)
- [ ] Migrasyon güncelleme adımı (`alembic upgrade head`)

#### 6.4 Belgelendirme

- [ ] `README.md`: Sistem genel bakışı, hızlı başlangıç
- [ ] `docs/README_KOORDINATOR_KURULUM.md`: Koordinatör kurulum kılavuzu
- [ ] `docs/README_ISCI_KURULUM.md`: İşçi kurulum kılavuzu
- [ ] `docs/SORUN_GIDERME.md`: Yaygın sorunlar ve çözümler

---

## Faz 7: Test ve Güçlendirme (Hafta 8–9)

### Amaç
Sistemi üretime hazır hale getirmek.

### 7.1 Birim Testleri

```
tests/
├── coordinator/
│   ├── test_job_queue.py          ← İş alma, race condition
│   ├── test_worker_monitor.py     ← Kalp atışı zaman aşımı
│   ├── test_file_watcher.py       ← Dosya algılama, MD5
│   ├── test_file_serving.py       ← Yol güvenliği, Range istekleri
│   └── test_api_jobs.py           ← İş API uç noktaları
├── worker/
│   ├── test_discovery.py          ← mDNS keşfi (mock)
│   ├── test_transcriber.py        ← Alt süreç başlatma, sinyal gönderme
│   ├── test_srt_generator.py      ← SRT biçimlendirme, Türkçe karakterler
│   ├── test_json_generator.py     ← JSON çıktı yapısı
│   └── test_reconnect.py          ← Geri çekilme hesaplama
└── integration/
    ├── test_end_to_end.py         ← Tam iş işleme (gerçek dosya)
    ├── test_worker_failure.py     ← Çökme ve kurtarma
    └── test_concurrent_workers.py ← Çoklu işçi eş zamanlılığı
```

**Test Hedefleri:**
- Birim test kapsamı: %80+
- Tüm mutlu yol senaryoları: geçiyor
- Tüm hata yolu senaryoları: geçiyor

### 7.2 Entegrasyon Test Senaryoları

| Senaryo | Beklenen Davranış |
|---|---|
| 3 işçi + 100 iş kuyruğu | Tüm işler sırasıyla tamamlanıyor |
| İşlem sırasında koordinatör yeniden başlatma | İşçiler yeniden bağlanıyor, işler kurtarılıyor |
| İşlem sırasında işçi çökmesi | İş 60s içinde yeniden kuyruğa alınıyor |
| Uzun süreli ağ kesintisi (>90s) | Zaman aşımı tespiti, iş yeniden atama |
| Duraklatma/devam döngüsü | Kaldığı yerden doğru devam |
| Bozuk MP3 dosyası | 3 denemeden sonra `failed`, dashboard uyarısı |
| Aynı dosyanın iki kez eklenmesi | Yalnızca bir iş oluşturuluyor (MD5 yinelemesi) |
| 5'ten fazla işçiyle eş zamanlı iş talebi | Race condition yok, her işçi farklı iş alıyor |

### 7.3 Türkçe Transkripsiyon Kalite Değerlendirmesi

- Değerlendirme seti: min 10 çeşitli Türkçe ses dosyası
- Kriterler: WER (Kelime Hata Oranı), zaman damgası doğruluğu, noktalama
- Kabul kriteri: WER < %15 (kalibre edilmiş kaynak için)
- Özel testler: Türkçe özel karakterler (ş, ğ, ü, ö, ı, ç), uzun kelimeler, teknik terimler

### 7.4 Performans Testleri

- RTF (Gerçek Zaman Faktörü) kıyaslama: M2 Max, M3 Max, M4 Max (her biri için)
- 3 işçiyle beklenen verim:
  - M3 Max: ~0.35–0.40 RTF → 1 saatlik sesi ~21-24 dakikada işler
- Bellek kullanımı: model yüklemesi sırasında maksimum RAM
- Koordinatör: 10 eş zamanlı WebSocket bağlantısı, 1000 req/dakika

### 7.5 Son Güçlendirme Listesi

- [ ] Tüm API uç noktalarında hata yanıtları tutarlı
- [ ] Log rotasyonu: `newsyslog.conf` veya logrotate
- [ ] PostgreSQL bağlantı havuzu iyi yapılandırılmış (min/max)
- [ ] WebSocket bağlantı sızıntısı yok (kenar çubuğu sayaçları her zaman kapanıyor)
- [ ] `temp_dir` temizliği koordinatör yeniden başlatmada çalışıyor
- [ ] Dosya yüklemesinde akış kullanılıyor (büyük dosyalar belleğe alınmıyor)
- [ ] Tüm veritabanı sorguları `EXPLAIN ANALYZE` ile doğrulandı
- [ ] Dashboard'da hiçbir Türkçe karakter bozulması yok

---

## Bağımlılık Sırası

```
Faz 1 ──► Faz 2 ──► Faz 3 ──► Faz 4
                │              │
                └──► Faz 5 ────┘
                         │
                         ▼
                       Faz 6 ──► Faz 7
```

Faz 5 (Dashboard), Faz 3 ve 4 ile **paralel** geliştirilebilir çünkü mock API veriyle çalışmaya başlayabilir.

---

## Riskler ve Azaltma Stratejileri

| Risk | Olasılık | Etki | Azaltma |
|---|---|---|---|
| mlx-whisper API değişikliği | Orta | Yüksek | Sürümü kilitle; wrapper katmanı ekle |
| Apple Silicon GPU API erişimi kısıtlaması | Düşük | Düşük | GPU metrikleri isteğe bağlı; sistem normal çalışır |
| PostgreSQL `FOR UPDATE SKIP LOCKED` beklentiden yavaş | Düşük | Orta | Önceden test et; gerekirse job_id aralıklarına böl |
| mDNS çok noktaya yayın engellenen ağ | Orta | Orta | Manuel IP yapılandırma seçeneği mevcut |
| macOS güncellemesi launchd davranışını değiştirir | Düşük | Orta | launchd plist'leri macOS 14+ için test et |
| Paket boyutu çok büyük (~3.5GB) | Düşük | Orta | USB dağıtım rehberi; model ayrı paket seçeneği |
| Türkçe kelime hata oranı kabul kriterin üzerinde | Orta | Yüksek | Medium → Large model yükseltme yolu belgele |

---

## Teknik Borç Kuyruğu (v2 için)

Bu özellikler bilinçli olarak kapsam dışında tutulmuştur ve gelecek sürümlerde düşünülebilir:

| Özellik | Neden Ertelendi |
|---|---|
| Koordinatör yüksek erişilebilirliği | Tek koordinatör yeterli; HA karmaşıklık artırır |
| PostgreSQL read replica | Mevcut hacimde gereksiz |
| GPU bellek izleme (Metal API) | `ioreg` yeterli; Metal API sudo gerektirir |
| Kimlik doğrulama | Kasıtlı olarak dahil edilmedi |
| Dosya format desteği (WAV, M4A vb.) | Gereksinim MP3 ile sınırlı; ffmpeg dönüştürücü eklenebilir |
| Özel dil desteği | Türkçe sabitlendi; `language` parametresi genişletmeye açık |
| Whisper Large-v3 desteği | Medium yeterli; farklı model yolu ile kolayca değiştirilebilir |

---

## Teslim Listesi (Faz 7 Sonunda)

- [ ] Tüm 7 belge tamamlandı ve güncel
- [ ] `coordinator-v1.0.0-arm64.tar.gz` — koordinatör kurulum paketi
- [ ] `worker-v1.0.0-arm64.tar.gz` — işçi kurulum paketi (~3.5 GB)
- [ ] `worker-update-v1.0.0-arm64.tar.gz` — model olmadan güncelleme paketi (~400 MB)
- [ ] `docs/README_KOORDINATOR_KURULUM.md` — kurulum kılavuzu
- [ ] `docs/README_ISCI_KURULUM.md` — kurulum kılavuzu
- [ ] `docs/SORUN_GIDERME.md` — sorun giderme rehberi
- [ ] Birim ve entegrasyon test paketi (CI'da geçiyor)
- [ ] Türkçe transkripsiyon kalite raporu
- [ ] Performans kıyaslama raporu (RTF değerleri)

---

*Bu belge uygulama ilerledikçe güncellenmelidir.*
