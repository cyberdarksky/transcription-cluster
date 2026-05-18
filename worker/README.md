# Transkripsiyon Kümesi — İşçi Ajanı

Apple Silicon Mac Studio için üretim kalitesinde dağıtık transkripsiyon işçisi.

## Gereksinimler

- **macOS:** 14+ (Sonoma)
- **Donanım:** Apple Silicon (M1/M2/M3/M4)
- **Python:** 3.11+
- **Model:** Whisper Medium MLX (koordinatör paketinden kopyalanır)

## Hızlı Başlangıç

```bash
# Kurulum (bir kez)
chmod +x install.sh
./install.sh

# Durum kontrolü
sudo launchctl list com.transcription.worker

# Loglar
tail -f /var/log/transcription-worker/worker.log
```

## Geliştirme

```bash
# Sanal ortam oluştur
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# .env dosyasını oluştur
cp .env.example .env
# nano .env  (isteğe bağlı: COORDINATOR_HOST ayarla)

# Çalıştır
python3 -m agent.main
```

## Dizin Yapısı

```
worker/
├── agent/
│   ├── main.py              # Giriş noktası, yaşam döngüsü, sinyal işleme
│   ├── config.py            # Pydantic Settings, stabil UUID
│   ├── state.py             # İşçi çalışma zamanı durumu
│   ├── discovery.py         # mDNS koordinatör keşfi (Zeroconf)
│   ├── coordinator_client.py # HTTP istemcisi (kayıt, kalp atışı, işler)
│   ├── websocket_client.py  # WebSocket komut kanalı (duraklat/devam/iptal)
│   ├── heartbeat.py         # Arkaplan kalp atışı görevi (kira yenileme)
│   ├── job_runner.py        # İş orkestrasyonu (6 aşama)
│   ├── downloader.py        # MP3 indirme (devam destekli)
│   ├── transcriber.py       # mlx-whisper alt süreci (SIGSTOP/SIGCONT)
│   ├── srt_generator.py     # SRT çıktı oluşturma
│   ├── json_generator.py    # JSON transkript oluşturma
│   ├── uploader.py          # Çok parçalı form yükleme
│   ├── metrics.py           # Sistem metrikleri (CPU/RAM/GPU)
│   ├── logging_config.py    # JSON yapılandırılmış günlük
│   └── cleanup.py           # Geçici dosya yönetimi
├── requirements.txt
├── .env.example
└── install.sh
```

## İşçi Akışı

```
1. BAŞLANGIÇ
   ├── mDNS keşfi (veya COORDINATOR_HOST ortam değişkeni)
   ├── Koordinatöre kayıt (stabil UUID ile)
   ├── Kalp atışı görevi başlatılır
   └── WebSocket bağlantısı kurulur

2. İŞ DÖNGÜSÜ (tekrar)
   ├── GET /api/v1/worker/jobs/next  → İş talep et
   │     204: kuyruk boş → 5s bekle → tekrar dene
   │     200: iş atandı →
   │
   ├── advance_state → DOWNLOADING
   ├── MP3 indir (devam destekli)
   │
   ├── advance_state → PROCESSING
   ├── mlx-whisper alt süreci başlat
   │     PAUSE_JOB → SIGSTOP  (alt süreci dondur)
   │     RESUME_JOB → SIGCONT (alt süreçten devam et)
   │     CANCEL_JOB → SIGTERM/SIGKILL
   │
   ├── SRT dosyası oluştur
   ├── JSON dosyası oluştur
   │
   ├── advance_state → UPLOADING
   ├── SRT + JSON yükle (multipart)
   │
   └── Geçici dosyaları temizle → tekrar 2'ye dön

3. KAPATMA (SIGTERM)
   ├── Mevcut işi bitir (veya başarısız olarak raporla)
   ├── Geçici dosyaları temizle
   └── Çık
```

## Duraklatma / Devam

Dashboard'dan duraklat tuşuna basıldığında:

1. Koordinatör WebSocket üzerinden `PAUSE_JOB` gönderir
2. İşçi `SIGSTOP` ile mlx-whisper alt sürecini dondurur
3. Metal GPU tensörleri bellekte kalmaya devam eder
4. İşçi `PAUSED` durumunu kalp atışıyla raporlar
5. `RESUME_JOB` geldiğinde `SIGCONT` ile devam edilir

Gerçek OS seviyesinde duraklatma — bellek veya hesaplama durumu kaybı olmaz.

## Hata Kategorileri

| Kategori | Örnekler | Davranış |
|---|---|---|
| `transient` | OOM, ağ hatası, işçi çökmesi | Yeniden deneme (0s, 60s, 300s) |
| `deterministic` | Bozuk MP3, desteklenmeyen format | Hemen FAILED, retry yok |

## Kira Sistemi

Her iş atanmasında koordinatör bir **kira** oluşturur (varsayılan: 300 saniye).
İşçi her kalp atışında kirayı yeniler. Yenilemezse (işçi çöktü veya iş çok uzun sürdü),
koordinatörün `LeaseRecoveryService`'i iş 30 saniyede bir tarayarak
süresi dolmuş kiralı işleri yeniden kuyruğa alır.

## Ortam Değişkenleri

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `COORDINATOR_HOST` | — (mDNS) | Koordinatör IP adresi |
| `COORDINATOR_PORT` | `8080` | Koordinatör HTTP portu |
| `MODEL_PATH` | `/opt/transcription-models/whisper-medium-mlx` | MLX model dizini |
| `WHISPER_LANGUAGE` | `tr` | Transkripsiyon dili |
| `TEMP_DIR` | `/tmp/transcription-jobs` | Geçici dosya dizini |
| `LOG_LEVEL` | `INFO` | Günlük seviyesi |
| `HEARTBEAT_INTERVAL_SECONDS` | `30` | Kalp atışı aralığı |
| `JOB_POLL_INTERVAL_SECONDS` | `5` | Boş kuyrukta bekleme |

## Sorun Giderme

**İşçi koordinatörü bulamıyor:**
```bash
# mDNS sinyali var mı?
dns-sd -B _transcription._tcp local

# Manuel IP ayarla
nano /opt/transcription-worker/.env
# COORDINATOR_HOST=192.168.1.101
sudo launchctl kickstart -k system/com.transcription.worker
```

**Model bulunamıyor:**
```bash
ls /opt/transcription-models/whisper-medium-mlx/
# config.json, model.safetensors, tokenizer.json görünmeli
```

**Bellek yetersiz (OOM):**
```bash
# Whisper Medium, tipik olarak ~3-4 GB RAM kullanır
# Mac Studio'nuzun yeterli unified memory'si olduğundan emin olun
vm_stat | grep "Pages free"
```
