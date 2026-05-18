# ARCHITECTURE.md
# Dağıtık Çevrimdışı Transkripsiyon Kümesi — Sistem Mimarisi

**Versiyon:** 1.0  
**Tarih:** Mayıs 2026  
**Hedef Platform:** Apple Silicon Mac Studio (M2 / M3 / M4)

---

## 1. Genel Bakış

Bu sistem, yerel ağda birbirine bağlı birden fazla Mac Studio'nun Whisper Medium modelini kullanarak MP3 dosyalarını Türkçe olarak transkribe ettiği, merkezi koordinasyon ve gerçek zamanlı izleme sağlayan üretim kalitesinde bir dağıtık işlem kümesidir.

### 1.1 Temel Tasarım İlkeleri

| İlke | Karar | Gerekçe |
|---|---|---|
| **Çevrimdışı-Önce** | Kurulumdan sonra internet bağlantısı gerekmez | Tüm modeller, bağımlılıklar yerel olarak paketlenir |
| **Durum Koordinatörde** | Tüm iş durumu PostgreSQL'de tutulur | İşçi çökmesi veya yeniden bağlanma durumunda durum kaybı yoktur |
| **Çekme Tabanlı Görev Dağıtımı** | İşçiler iş ister, koordinatör itmez | İşçi kapasitesi doğal geri baskısı sağlar |
| **Tek Parça İşleme** | Her MP3 tek bir iş olarak işlenir, parçalanmaz | Tutarsız segment birleştirme sorunlarından kaçınılır |
| **Kimlik Doğrulamasız** | Dahili ağ güven modeli | Kapalı LAN ortamında operasyonel sürtünme azaltılır |
| **Servis Keşfi** | mDNS / Zeroconf otomatik keşif | Sıfır konfigürasyonlu işçi ekleme |

---

## 2. Yüksek Seviyeli Mimari

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         YEREL AĞ (LAN / 1GbE veya 10GbE)               │
│                                                                         │
│  ┌────────────────────────────────────────┐                             │
│  │           KOORDİNATÖR DÜĞÜMÜ           │                             │
│  │          (Mac Studio — Ana)             │                             │
│  │                                         │                             │
│  │  ┌─────────────────────────────────┐   │                             │
│  │  │  Dosya İzleyici Servisi          │   │                             │
│  │  │  (watchdog)                      │   │                             │
│  │  │  Giriş dizinlerini izler,        │   │                             │
│  │  │  yeni MP3'leri algılar,          │   │                             │
│  │  │  iş kayıtları oluşturur          │   │                             │
│  │  └──────────────┬──────────────────┘   │                             │
│  │                 │                       │                             │
│  │  ┌──────────────▼──────────────────┐   │     ┌─────────────────────┐ │
│  │  │  FastAPI Koordinatör            │   │     │  Web Tarayıcısı     │ │
│  │  │  Servisi (:8080)                │◄──┼─────┤  (Herhangi bir      │ │
│  │  │                                 │   │ WS  │   cihazdaki)        │ │
│  │  │  • İş Kuyruğu Yönetimi          │   │     │  Dashboard Arayüzü  │ │
│  │  │  • İşçi Kaydı & Kalp Atışı     │   │     └─────────────────────┘ │
│  │  │  • Dosya Sunma (MP3 indirme)    │   │                             │
│  │  │  • Sonuç Alma (SRT/JSON yükleme)│   │                             │
│  │  │  • WebSocket Yayın Yöneticisi  │   │                             │
│  │  │  • mDNS Duyurusu               │   │                             │
│  │  └──────────────┬──────────────────┘   │                             │
│  │                 │                       │                             │
│  │  ┌──────────────▼──────────────────┐   │                             │
│  │  │  PostgreSQL 15+                 │   │                             │
│  │  │  (:5432)                        │   │                             │
│  │  │                                 │   │                             │
│  │  │  • jobs          • workers      │   │                             │
│  │  │  • job_events    • worker_metrics│  │                             │
│  │  │  • input_dirs    • system_cfg   │   │                             │
│  │  └─────────────────────────────────┘   │                             │
│  │                                         │                             │
│  │  ┌─────────────────────────────────┐   │                             │
│  │  │  Yerel Depolama                 │   │                             │
│  │  │  /input/...   (MP3 dosyaları)   │   │                             │
│  │  │  /output/...  (SRT, JSON)       │   │                             │
│  │  └─────────────────────────────────┘   │                             │
│  └────────────────────────────────────────┘                             │
│                          │                                               │
│              REST + WebSocket (HTTP/1.1)                                 │
│             mDNS Hizmet Keşfi (_transcription._tcp.local.)               │
│                          │                                               │
│          ┌───────────────┼───────────────┐                              │
│          │               │               │                              │
│  ┌───────▼─────┐ ┌───────▼─────┐ ┌──────▼──────┐                      │
│  │  İŞÇİ 1     │ │  İŞÇİ 2     │ │  İŞÇİ N     │                      │
│  │  Mac Studio │ │  Mac Studio │ │  Mac Studio │                      │
│  │             │ │             │ │             │                      │
│  │ İşçi Ajanı  │ │ İşçi Ajanı  │ │ İşçi Ajanı  │                      │
│  │ mlx-whisper │ │ mlx-whisper │ │ mlx-whisper │                      │
│  │ Whisper Med.│ │ Whisper Med.│ │ Whisper Med.│                      │
│  │             │ │             │ │             │                      │
│  │ /tmp/jobs/  │ │ /tmp/jobs/  │ │ /tmp/jobs/  │                      │
│  │ (geçici)    │ │ (geçici)    │ │ (geçici)    │                      │
│  └─────────────┘ └─────────────┘ └─────────────┘                      │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Bileşen Açıklamaları

### 3.1 Koordinatör Düğümü

Koordinatör, sistemin beynidir. Tek bir Mac Studio üzerinde çalışır ve tüm iş durumunu, işçi kayıtlarını ve dosyaları yönetir.

#### 3.1.1 FastAPI Koordinatör Servisi

- **Rol:** Merkezi API sunucusu, iş dağıtıcısı, dosya sunucusu
- **Port:** 8080 (HTTP + WebSocket)
- **Teknoloji:** Python 3.12 + FastAPI + Uvicorn (--workers 4, --loop uvloop)
- **Alt Bileşenler:**

| Alt Bileşen | Sorumluluk |
|---|---|
| İş API'si | İş CRUD, durum geçişleri, çıktı indirme |
| İşçi API'si | Kayıt, kalp atışı, iş talep etme, ilerleme raporlama |
| Dosya Sunucusu | Çalışanlara MP3 akışı, SRT/JSON alımı |
| WebSocket Yöneticisi | Dashboard ve işçi WebSocket bağlantılarını yönetir |
| İşçi Monitörü | Kalp atışı zaman aşımlarını algılar, ölü işçileri işaretler, işleri yeniden kuyruğa alır |
| İş Dağıtıcısı | Bekleyen işleri uygun işçilere atar |
| mDNS Duyurucusu | Koordinatörü `_transcription._tcp.local.` olarak ilan eder |

#### 3.1.2 Dosya İzleyici Servisi

- **Teknoloji:** Python watchdog kütüphanesi
- **İşlev:** Giriş dizinlerini (alt dizinler dahil) izler; yeni `.mp3` dosyası algılandığında bir iş kaydı oluşturur
- **Klasör Hiyerarşisi Korunması:** Girişe göre göreli yol hesaplanır ve veritabanında saklanır
- **Yineleme Tespiti:** Her dosya için MD5 karması hesaplanır; aynı karmalı dosya tekrar işlenmez

#### 3.1.3 PostgreSQL 15+

- **Rol:** Tek doğru kaynak; tüm iş ve işçi durumu
- **Bağlantı Havuzu:** asyncpg + SQLAlchemy 2.0 (async) ile 20 bağlantı havuzu
- **Kalıcılık:** fsync=on, WAL etkin, günlük CHECKPOINT

#### 3.1.4 Statik Dosya Sunucusu (Dashboard)

- Derlenen React uygulaması (`dist/`) koordinatörün `/` yolundan sunulur
- Ayrı bir frontend sunucusuna gerek yoktur

---

### 3.2 İşçi Düğümü

Her işçi, çalışmak için yalnızca koordinatörün IP adresine ihtiyaç duyan (mDNS ile otomatik bulur) bağımsız bir Python sürecidir.

#### 3.2.1 İşçi Ajanı

Durum Makinesi:
```
  [BAŞLANGIÇ]
       │
       ▼
  [KEŞİF]      ──── mDNS ile koordinatör bulunuyor
       │
       ▼
  [BAĞLANMA]   ──── REST + WebSocket bağlantısı kuruldu
       │
       ▼
  [KAYIT]      ──── POST /api/v1/worker/register
       │
       ▼
  [BOŞ]        ◄─── Yeni iş bekliyor (GET /api/v1/worker/jobs/next)
       │
       ▼
  [İNDİRME]   ──── MP3 dosyasını koordinatörden indiriyor
       │
       ▼
  [İŞLEME]    ──── mlx-whisper ile transkripsiyon yapıyor
       │                ├── [DURAKLATILDI] ── SIGSTOP ile askıya alındı
       │                └── [DEVAM]      ── SIGCONT ile devam etti
       ▼
  [YÜKLEME]   ──── SRT + JSON sonuçlarını koordinatöre gönderiyor
       │
       ▼
  [TAMAMLANDI] ──► geri [BOŞ]
       
  [HATA]       ──► yeniden deneme veya başarısız iş raporlama
  [YENİDEN BAĞLANMA] ── koordinatör bağlantısı kesildi, geri çekilme ile yeniden deneme
```

#### 3.2.2 mlx-whisper Çıkarım Motoru

- **Kütüphane:** `mlx-whisper` — Apple MLX çerçevesini kullanan, M1/M2/M3/M4 için yerel optimize edilmiş
- **Model:** `mlx-community/whisper-medium-mlx` (Hugging Face'den kurulum sırasında indirilir)
- **Dil:** `language="tr"` (Türkçe zorlamalı)
- **İşlem Modu:** Alt süreç olarak çalışır (gerçek duraklatma/devam için SIGSTOP/SIGCONT gerekir)
- **Çıktı:** Zaman damgalı segment listesi (JSON); SRT ve JSON ayrıştırması işçi tarafından yapılır

---

### 3.3 React Dashboard

- **Teknoloji:** React 18 + TypeScript + Vite + TailwindCSS v4 + shadcn/ui
- **Servis:** Koordinatör FastAPI'sinden statik dosyalar olarak sunulur (`/`)
- **Gerçek Zamanlı:** WebSocket (`/ws/dashboard`) — iş ve işçi güncellemeleri anlık yansır
- **Dil:** Tamamen Türkçe arayüz
- **Tema:** Varsayılan karanlık mod, açık mod geçiş seçeneği mevcut
- **Erişim:** LAN'daki herhangi bir cihazdan `http://<koordinatör-ip>:8080` ile

---

## 4. Ağ Topolojisi

### 4.1 Servis Keşfi (mDNS / Zeroconf)

```
Koordinatör başlar
    │
    ├── Zeroconf servisi kaydeder:
    │   Servis Türü : _transcription._tcp.local.
    │   Servis Adı  : TranscriptionCluster._transcription._tcp.local.
    │   Port        : 8080
    │   Özellikler  : version=1.0.0, name=<hostname>
    │
İşçi başlar
    │
    ├── _transcription._tcp.local. için mDNS'i dinler
    ├── Koordinatörü bulunca:
    │   ├── REST bağlantısı: http://<koordinatör-ip>:8080
    │   ├── WebSocket bağlantısı: ws://<koordinatör-ip>:8080/ws/worker
    │   └── Koordinatör IP'sini yerel yapılandırmaya kaydeder (önbellek)
    └── mDNS bulamazsa: kaydedilmiş IP ile yeniden dener (varsa)
```

### 4.2 Port Tahsisi

| Port | Hizmet | Açıklama |
|---|---|---|
| 5432 | PostgreSQL | Yalnızca koordinatör localhost |
| 8080 | FastAPI Koordinatör | Tüm ağ; REST + WebSocket |
| 5353/UDP | mDNS | Zeroconf keşfi, çok noktaya yayın |

### 4.3 Bant Genişliği Değerlendirmesi

- **MP3 İndirme:** İşçi başına ~1-15 MB/dosya (tipik konuşma MP3'ü)
- **Sonuç Yükleme:** İşçi başına ~5-50 KB/dosya (SRT + JSON hafiftir)
- **Kalp Atışı:** 30 saniyede bir; ~200 bayt JSON
- **WebSocket Akışı:** ~100 bayt/ilerleme güncellemesi; saniyede birkaç kez

1GbE LAN için bile bant genişliği sorun değildir.

---

## 5. Veri Akışı

### 5.1 Dosyadan Sonuca — Tam Akış

```
1. DOSYA ALGILAMA
   ─────────────
   Kullanıcı MP3'ü /input/Proje_A/toplanti.mp3 yoluna koyar
   Dosya İzleyici → MD5 hesapla → DB'de yok mu kontrol et
   → jobs tablosuna INSERT (status='pending')
   → WebSocket dashboard'a yayınla: {event: 'job_created', job_id: ...}

2. İŞ TALEP ETME
   ──────────────
   İşçi (boş) → GET /api/v1/worker/jobs/next
   Koordinatör:
     - SELECT job WHERE status='pending' ORDER BY priority DESC, created_at ASC LIMIT 1
     - Optimistik kilitleme: UPDATE status='assigned', worker_id=?, assigned_at=now()
     - İşçiye 200 OK + iş detayları dönüyor
   → WebSocket dashboard'a yayınla: {event: 'job_assigned', job_id: ..., worker_id: ...}

3. DOSYA İNDİRME
   ──────────────
   İşçi → GET /api/v1/files/{job_id}/download
   Koordinatör → /input/... dosyasını akış olarak sunar
   İşçi → /tmp/jobs/{job_id}/input.mp3 olarak kaydeder

4. TRANSKRİPSİYON
   ────────────────
   İşçi → POST /api/v1/worker/jobs/{job_id}/start
   İşçi → mlx-whisper alt sürecini başlatır:
     mlx_whisper.transcribe(
       audio_path="/tmp/jobs/{job_id}/input.mp3",
       path_or_hf_repo="mlx-community/whisper-medium-mlx",
       language="tr",
       word_timestamps=True,
       verbose=False
     )
   İşçi → her 10 saniyede bir ilerleme tahmini gönderir
     POST /api/v1/worker/jobs/{job_id}/progress {percent: 42.5}
   → WebSocket dashboard'a yayınla: {event: 'job_progress', ...}

5. SONUÇ OLUŞTURMA
   ─────────────────
   mlx-whisper tamamlanır → segment listesi döner
   İşçi:
     - SRT dosyası oluşturur (/tmp/jobs/{job_id}/output.srt)
     - JSON dosyası oluşturur (/tmp/jobs/{job_id}/output.json)

6. SONUÇ YÜKLEME
   ──────────────
   İşçi → POST /api/v1/worker/jobs/{job_id}/results (multipart: srt + json)
   Koordinatör:
     - /output/Proje_A/toplanti.srt yoluna kaydeder (girişin klasör hiyerarşisini korur)
     - /output/Proje_A/toplanti.json yoluna kaydeder
     - jobs tablosunu günceller: status='completed', output_srt_path=..., completed_at=now()
   → WebSocket dashboard'a yayınla: {event: 'job_completed', ...}

7. TEMİZLİK
   ─────────
   İşçi → /tmp/jobs/{job_id}/ dizinini siler
   İşçi → Sonraki iş için [BOŞ] durumuna döner
```

### 5.2 Kalp Atışı ve İzleme

```
Her 30 saniyede bir:
İşçi → POST /api/v1/worker/heartbeat
  {
    "worker_id": "...",
    "status": "processing",
    "current_job_id": "...",
    "job_progress_percent": 42.5,
    "metrics": {
      "cpu_percent": 85.2,
      "memory_used_gb": 12.4,
      "memory_total_gb": 32.0,
      "gpu_utilization_percent": 92.1
    }
  }

Koordinatör İşçi Monitörü (her 15 saniyede kontrol eder):
  - last_heartbeat > 90 saniye önce ise → işçiyi 'offline' işaretle
  - Etkilenen iş varsa:
    - İşi 'pending' olarak geri kuyruğa al
    - retry_count artır
    - retry_count >= max_retries ise: status='failed'
  → WebSocket dashboard'a yayınla: {event: 'worker_offline', worker_id: ...}
  → WebSocket dashboard'a yayınla: {event: 'job_requeued', job_id: ...}
```

---

## 6. Hata Toleransı Tasarımı

### 6.1 Hata Senaryoları ve Kurtarma

| Hata Senaryosu | Algılama | Kurtarma |
|---|---|---|
| İşçi çöküyor (işlem sırasında) | Kalp atışı 90s zaman aşımı | İş yeniden kuyruğa alınır, başka işçiye atanır |
| İşçi ağ bağlantısı kopuyor | Kalp atışı 90s zaman aşımı + TCP RST | İşçi geri çekilme ile yeniden bağlanır; tamamlıyorsa rapor eder |
| Koordinatör yeniden başlatılıyor | İşçi HTTP 503/bağlantı hatası alır | İşçi geri çekilme döngüsü; koordinatör yeniden başladığında DB'den durum alır |
| PostgreSQL yeniden başlatılıyor | FastAPI bağlantı havuzu hatası | SQLAlchemy otomatik yeniden bağlanma; bekleyen istekler başarısız oluyor |
| Dosya bozuk/işlenemeyen MP3 | mlx-whisper Exception | İş 'failed' olarak işaretlenir, hata kaydedilir; max_retries'e kadar yeniden denenir |
| Disk dolu (koordinatörde) | IOError yükleme sırasında | İş başarısız, disk uyarısı dashboard'a gönderilir |
| İşçi çıkarım OOM | İşçi alt süreci çöker | İşçi ajanı algılar, koordinatöre raporlar, iş yeniden kuyruğa alınır |

### 6.2 Yeniden Deneme Politikası

```
Her iş için:
  max_retries = 3 (yapılandırılabilir)
  retry_count: 0..max_retries

Yeniden deneme zamanlaması:
  1. deneme: 0 saniye bekleme (hemen yeniden kuyruğa)
  2. deneme: 60 saniye bekleme (geçici sorunlara karşı)
  3. deneme: 300 saniye bekleme

max_retries aşıldıktan sonra: status='failed', dashboard'da görünür
Manuel yeniden deneme: dashboard üzerinden tetiklenebilir (retry_count sıfırlanır)
```

### 6.3 Koordinatör Durumunun Korunması

Koordinatör tamamen durumsuz bir HTTP katmanıdır; tüm durum PostgreSQL'dedir:

- Koordinatör yeniden başladığında: tüm `assigned` işler `pending` olarak sıfırlanır (başlangıç rutini)
- Aktif işçiler yeniden bağlanır ve hâlâ çalışan işlerini raporlar
- Koordinatör, işçi raporunu kabul eder ve iş durumunu `processing` olarak günceller

---

## 7. Teknoloji Yığını

### 7.1 Koordinatör

| Katman | Teknoloji | Versiyon | Gerekçe |
|---|---|---|---|
| HTTP Çerçevesi | FastAPI | 0.115+ | Async WebSocket desteği, Pydantic v2 entegrasyonu |
| ASGI Sunucusu | Uvicorn | 0.30+ | uvloop ile yüksek performanslı async I/O |
| ORM | SQLAlchemy | 2.0+ | Async engine, tip güvenli sorgular |
| DB Sürücüsü | asyncpg | 0.29+ | PostgreSQL için en hızlı async Python sürücüsü |
| Migration | Alembic | 1.13+ | Şema versiyonlama, geri alma desteği |
| Veritabanı | PostgreSQL | 15+ | JSONB, güvenilir queue semantiği, olgun ekosistem |
| Doğrulama | Pydantic | 2.x | Hızlı, tip güvenli model doğrulama |
| Dosya İzleme | watchdog | 4.x | Çapraz platform, inotify/kqueue tabanlı |
| mDNS | zeroconf | 0.131+ | Saf Python Zeroconf/mDNS |
| Servis Yönetimi | launchd | macOS | macOS için yerel servis yöneticisi |

### 7.2 İşçi

| Katman | Teknoloji | Versiyon | Gerekçe |
|---|---|---|---|
| Çıkarım | mlx-whisper | 0.4+ | Apple Silicon Metal GPU yerel optimize |
| MLX Çerçevesi | mlx | 0.16+ | Apple Silicon için Apple'ın ML çerçevesi |
| HTTP İstemcisi | httpx | 0.27+ | Async, akış indirme desteği |
| WebSocket | websockets | 12+ | Koordinatöre güvenilir WS bağlantısı |
| mDNS | zeroconf | 0.131+ | Koordinatör keşfi |
| Süreç Yönetimi | launchd | macOS | Önyükleme sırasında otomatik başlatma |

### 7.3 Dashboard

| Katman | Teknoloji | Versiyon | Gerekçe |
|---|---|---|---|
| UI Çerçevesi | React | 18.3+ | Geniş ekosistem, kanca tabanlı reaktivite |
| Dil | TypeScript | 5.x | Üretim kalitesi için tip güvenliği |
| Derleme Aracı | Vite | 5.x | Hızlı geliştirme, optimize üretim derlemesi |
| Stil | TailwindCSS | 4.x | Utility-first; kolay karanlık mod |
| Bileşenler | shadcn/ui | güncel | Erişilebilir, özelleştirilebilir bileşen kütüphanesi |
| Durum | Zustand | 4.x | Minimal, sezgisel global state |
| Veri Getirme | TanStack Query | 5.x | Cache, yeniden deneme, arka plan yenileme |
| Grafikler | Recharts | 2.x | React-native SVG grafikler |
| Yönlendirme | React Router | 6.x | İstemci tarafı yönlendirme |
| Gerçek Zamanlı | Yerel WebSocket API | — | TanStack Query ile entegre |

---

## 8. Güvenlik Değerlendirmeleri (Kimlik Doğrulamasız)

Sistem kasıtlı olarak kimlik doğrulamasız tasarlanmıştır. Bu nedenle:

1. **Ağ Seviyesinde Yalıtım:** Koordinatör yalnızca LAN'a maruz kalmalıdır; asla genel internete açılmamalıdır
2. **Güvenlik Duvarı Kuralları:** Koordinatörün 8080 portuna yalnızca yerel alt ağ erişebilmelidir (`192.168.x.x/24` veya benzeri)
3. **Giriş Doğrulama:** Tüm API uç noktaları Pydantic doğrulaması kullanır; yol geçişi saldırılarına karşı dosya yolları temizlenir
4. **SQL Enjeksiyonu:** Parametreli SQLAlchemy sorguları; ham SQL yok
5. **Dosya Yolu Güvenliği:** Hizmet edilen tüm dosya yolları köklendirilir ve `..` geçişi engellenir

---

## 9. Ölçeklenebilirlik Sınırları

Bu mimari şu kapasiteler için tasarlanmıştır:

| Boyut | Beklenen Sınır | Değerlendirme |
|---|---|---|
| Eş zamanlı işçiler | 1–20 | PostgreSQL row-level locking; iş koordinasyonu için yeterli |
| Bekleyen iş kuyruğu | 50.000+ iş | Tek bir PostgreSQL tablosu; indeksler performansı korur |
| Eş zamanlı WebSocket bağlantısı | 100+ | Uvicorn async; pratik sınır yoktur |
| Dosya boyutu | 2 GB'a kadar | Akış aktarımı; bellek sorun değil |
| Toplam işlenen dosya | Sınırsız | Hiçbir zaman silinmeyen kayıtlar; disk kapasitesi sınırlayıcıdır |

20'den fazla işçi veya çok daha yüksek iş hacmi için:
- Koordinatör PostgreSQL → okuma replikası ile ölçeklendirilebilir
- FastAPI → birden fazla Uvicorn işçisi ile ölçeklendirilebilir (zaten desteklenmekte)
- İş kuyruğu → öncelik indeksleri ile optimize edilebilir

---

## 10. Dağıtım Topolojisi (Öneri)

```
Mac Studio 1 (Koordinatör)
  ├── macOS 14+ (Sonoma veya üzeri)
  ├── PostgreSQL 15 (Homebrew, launchd servisi)
  ├── FastAPI Koordinatör (launchd servisi, port 8080)
  ├── React Dashboard (FastAPI'den statik olarak sunuluyor)
  └── /Volumes/Data/transcription/ (giriş/çıkış dizinleri)

Mac Studio 2..N (İşçiler)
  ├── macOS 14+
  ├── İşçi Ajanı (launchd servisi)
  ├── mlx-whisper + model (kurulum sırasında indirildi)
  └── /tmp/transcription-jobs/ (geçici işlem dizini)

Ağ
  ├── 1GbE veya 10GbE LAN switch
  ├── Statik IP'ler veya DHCP rezervasyonu (koordinatör için önerilir)
  └── mDNS multicast yayını etkin (çoğu LAN switchinde varsayılan)
```

---

*Sonraki belge: [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md)*
