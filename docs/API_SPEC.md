# API_SPEC.md
# REST ve WebSocket API Spesifikasyonu

**Temel URL:** `http://<koordinatör-ip>:8080`  
**API Versiyonu:** v1  
**İçerik Tipi:** `application/json` (dosya yükleme için `multipart/form-data`)  
**Kimlik Doğrulama:** Yok (LAN güven modeli)

---

## 1. API Genel Bakış

```
REST Uç Noktaları
├── /api/v1/jobs/*           → İş yönetimi (Dashboard tarafından kullanılır)
├── /api/v1/workers/*        → İşçi yönetimi (Dashboard tarafından kullanılır)
├── /api/v1/worker/*         → İşçi iç API'si (İşçi ajanları tarafından kullanılır)
├── /api/v1/files/*          → Dosya indirme/yükleme
├── /api/v1/system/*         → Sistem istatistikleri ve yapılandırma
└── /api/v1/scan             → Dizin taramasını tetikler

WebSocket Uç Noktaları
├── /ws/dashboard            → Dashboard gerçek zamanlı akışı
└── /ws/worker               → İşçi komut kanalı
```

---

## 2. Ortak Türler ve Şemalar

### 2.1 Sayfalama

Listeleme uç noktalarında kullanılan standart sorgu parametreleri:

```
page     : integer, min=1, default=1
page_size: integer, min=1, max=200, default=50
```

Yanıt zarfı:

```json
{
  "items": [...],
  "total": 1432,
  "page": 1,
  "page_size": 50,
  "pages": 29
}
```

### 2.2 Hata Yanıtı

```json
{
  "detail": "İş bulunamadı",
  "error_code": "JOB_NOT_FOUND",
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

HTTP durum kodları:
- `200` Başarılı
- `201` Oluşturuldu
- `400` Hatalı İstek (doğrulama hatası)
- `404` Bulunamadı
- `409` Çakışma (zaten var)
- `422` İşlenemeyen Varlık (Pydantic doğrulama hatası)
- `500` Sunucu Hatası

### 2.3 İş Durumu Numaralandırması

```
pending    → İşçi atanmayı bekliyor
assigned   → İşçiye atandı, henüz başlamadı
processing → Aktif transkripsiyon devam ediyor
paused     → İşçi süreci askıya alındı (SIGSTOP)
completed  → SRT ve JSON başarıyla üretildi
failed     → Tüm yeniden denemeler tükendi
cancelled  → Dashboard aracılığıyla kullanıcı tarafından iptal edildi
```

### 2.4 İşçi Durumu Numaralandırması

```
online   → Bağlandı, kayıt süreci devam ediyor
idle     → Bağlı ve iş bekliyor
busy     → Aktif iş işliyor
paused   → Mevcut iş duraklatıldı
offline  → Kalp atışı zaman aşımı veya düzgün bağlantı kesilmesi
error    → Kritik hata, müdahale gerekiyor
```

---

## 3. İş API'si — Dashboard Kullanımı

### 3.1 İşleri Listele

```
GET /api/v1/jobs
```

**Sorgu Parametreleri:**

| Parametre | Tür | Açıklama |
|---|---|---|
| `status` | string | Duruma göre filtrele (birden fazla: `?status=pending&status=failed`) |
| `worker_id` | UUID | Belirli işçiye atanmış işler |
| `folder` | string | `relative_folder` ile benzerlik araması |
| `filename` | string | `original_filename` ile benzerlik araması |
| `sort` | string | `created_at_desc`, `created_at_asc`, `priority_desc`, `completed_at_desc` |
| `page` | int | Sayfa numarası |
| `page_size` | int | Sayfa boyutu |

**Yanıt:** `200 OK`

```json
{
  "items": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440001",
      "input_path": "Proje_A/toplanti_2024.mp3",
      "original_filename": "toplanti_2024.mp3",
      "relative_folder": "Proje_A",
      "status": "processing",
      "priority": 0,
      "retry_count": 0,
      "max_retries": 3,
      "progress_percent": 42.5,
      "file_size_bytes": 15728640,
      "worker_id": "550e8400-e29b-41d4-a716-446655440010",
      "worker_hostname": "mac-studio-2",
      "created_at": "2026-05-18T10:00:00Z",
      "assigned_at": "2026-05-18T10:01:00Z",
      "started_at": "2026-05-18T10:01:05Z",
      "completed_at": null,
      "audio_duration_seconds": 3642.5,
      "processing_time_seconds": null,
      "rtf": null,
      "output_srt_path": null,
      "output_json_path": null,
      "last_error": null
    }
  ],
  "total": 234,
  "page": 1,
  "page_size": 50,
  "pages": 5
}
```

---

### 3.2 İş Detayı

```
GET /api/v1/jobs/{job_id}
```

**Yanıt:** `200 OK` — Tam iş nesnesi (3.1 ile aynı şema + aşağıdaki)

```json
{
  "...",
  "events": [
    {
      "id": 1001,
      "event_type": "created",
      "worker_id": null,
      "details": {"previous_status": null, "new_status": "pending"},
      "created_at": "2026-05-18T10:00:00Z"
    },
    {
      "id": 1002,
      "event_type": "assigned",
      "worker_id": "550e8400-e29b-41d4-a716-446655440010",
      "details": {"previous_status": "pending", "new_status": "assigned"},
      "created_at": "2026-05-18T10:01:00Z"
    }
  ]
}
```

---

### 3.3 İşi Duraklat

```
POST /api/v1/jobs/{job_id}/pause
```

**İstek Gövdesi:** Yok

**Davranış:**
1. **Durum geçiş doğrulaması** — SQL seviyesinde zorunlu:
   ```sql
   UPDATE jobs SET status='paused', paused_at=NOW(), updated_at=NOW()
   WHERE id=$1 AND status='processing'  -- yalnızca 'processing' duraklatılabilir
   RETURNING id
   ```
   0 satır etkilendiyse → 409 döner (yanlış durum geçişi)
2. `worker_id` NULL değilse: WebSocket üzerinden `PAUSE_JOB` komutu gönderilir
3. WebSocket bağlantısı yoksa: `pending_commands` kuyruğuna eklenir (kalp atışı yanıtında teslim)
4. İşçi SIGSTOP alır; Metal GPU bağlamı belleğe alınmış halde askıda kalır

**Yanıt:** `200 OK`

```json
{
  "id": "550e8400-...",
  "status": "paused",
  "command_delivered": true,
  "message": "Duraklat komutu işçiye gönderildi"
}
```

`command_delivered: false` → İşçiye WebSocket üzerinden ulaşılamadı; kalp atışında teslim edilecek.

**Hata Yanıtları:**
- `404` İş bulunamadı
- `409` İş şu anda duraklatılabilir durumda değil (yalnızca `processing` durumundaki işler duraklatılabilir)

---

### 3.4 İşi Devam Ettir

```
POST /api/v1/jobs/{job_id}/resume
```

**Davranış:**
1. İşin durumunu `processing` olarak günceller
2. Atanan işçiye WebSocket üzerinden `RESUME_JOB` komutu gönderir
3. İşçi alt süreci SIGCONT ile devam ettirir

**Yanıt:** `200 OK`

```json
{
  "id": "550e8400-...",
  "status": "processing",
  "message": "Devam komutu işçiye gönderildi"
}
```

---

### 3.5 İşi İptal Et

```
POST /api/v1/jobs/{job_id}/cancel
```

**Davranış:**
1. İşçiye `CANCEL_JOB` WebSocket komutu gönderir (varsa)
2. İşin durumunu `cancelled` olarak günceller
3. İşçi atanması kaldırılır

**Yanıt:** `200 OK`

---

### 3.6 İşi Yeniden Dene

```
POST /api/v1/jobs/{job_id}/retry
```

**Yalnızca `failed` veya `cancelled` işler için geçerlidir.**

**Davranış:**
1. `retry_count` sıfırlanır
2. `last_error` temizlenir
3. `status` → `pending` olarak ayarlanır

**Yanıt:** `200 OK`

```json
{
  "id": "550e8400-...",
  "status": "pending",
  "retry_count": 0
}
```

---

### 3.7 İş Çıktılarını İndir

```
GET /api/v1/jobs/{job_id}/output/srt
GET /api/v1/jobs/{job_id}/output/json
```

**Yanıt:** `200 OK` — Dosya içeriği (Content-Disposition: attachment)

---

### 3.8 Toplu İş Oluşturma

```
POST /api/v1/jobs/bulk
```

**İstek Gövdesi:**

```json
{
  "input_directory_id": "550e8400-...",
  "priority": 10,
  "force_reprocess": false
}
```

`force_reprocess: true` ise daha önce tamamlanmış dosyalar dahil edilir.

**Yanıt:** `201 Created`

```json
{
  "created": 47,
  "skipped_duplicate": 12,
  "skipped_completed": 8,
  "total_scanned": 67
}
```

---

## 4. İşçi Yönetim API'si — Dashboard Kullanımı

### 4.1 İşçileri Listele

```
GET /api/v1/workers
```

**Yanıt:** `200 OK`

```json
{
  "items": [
    {
      "id": "550e8400-...",
      "hostname": "mac-studio-2",
      "ip_address": "192.168.1.102",
      "status": "busy",
      "cpu_model": "Apple M3 Max",
      "cpu_cores": 16,
      "memory_total_gb": 128.0,
      "gpu_model": "Apple M3 Max 40-core GPU",
      "whisper_backend": "mlx-whisper",
      "worker_version": "1.0.0",
      "last_heartbeat": "2026-05-18T10:05:30Z",
      "seconds_since_heartbeat": 12,
      "current_job_id": "550e8400-...",
      "current_job_path": "Proje_A/toplanti.mp3",
      "current_job_progress": 42.5,
      "jobs_completed": 234,
      "jobs_failed": 3,
      "total_audio_hours": 156.4,
      "average_rtf": 0.38,
      "last_cpu_percent": 85.2,
      "last_memory_percent": 61.3,
      "last_gpu_percent": 92.1,
      "registered_at": "2026-05-18T08:00:00Z"
    }
  ],
  "total": 3
}
```

---

### 4.2 İşçi Detayı

```
GET /api/v1/workers/{worker_id}
```

**Ek Alanlar:**

```json
{
  "...",
  "metrics_history": [
    {
      "recorded_at": "2026-05-18T10:05:30Z",
      "cpu_percent": 85.2,
      "memory_percent": 61.3,
      "gpu_percent": 92.1
    }
  ],
  "recent_jobs": [...]
}
```

---

### 4.3 İşçiyi Duraklat (Yeni İş Almayı Durdur)

```
POST /api/v1/workers/{worker_id}/pause
```

İşçiyi `paused` durumuna getirir; mevcut işini bitirir ancak yeni iş almaz.

---

### 4.4 İşçiyi Devam Ettir

```
POST /api/v1/workers/{worker_id}/resume
```

İşçiyi `idle` durumuna döndürür; yeni iş almaya başlar.

---

### 4.5 İşçi Metrik Geçmişi

```
GET /api/v1/workers/{worker_id}/metrics
```

**Sorgu Parametreleri:**

| Parametre | Varsayılan | Açıklama |
|---|---|---|
| `from` | 1 saat önce | ISO 8601 zaman damgası |
| `to` | şimdi | ISO 8601 zaman damgası |
| `resolution` | `1m` | Toplu aralık: `30s`, `1m`, `5m`, `15m`, `1h` |

**Yanıt:**

```json
{
  "worker_id": "...",
  "resolution": "1m",
  "metrics": [
    {
      "time": "2026-05-18T10:00:00Z",
      "cpu_percent": 84.5,
      "memory_percent": 61.2,
      "gpu_percent": 91.8
    }
  ]
}
```

---

## 5. İşçi İç API'si — Yalnızca İşçi Ajanları Tarafından Kullanılır

Bu uç noktalar yalnızca işçi ajanları tarafından çağrılır.

### 5.1 İşçi Kaydı

```
POST /api/v1/worker/register
```

**İstek Gövdesi:**

```json
{
  "stable_worker_id": "a7f3c291-1234-4abc-8def-000000000001",
  "hostname": "mac-studio-2",
  "mac_address": "A4:C3:F0:12:34:56",
  "ip_address": "192.168.1.102",
  "api_port": 8081,
  "cpu_model": "Apple M3 Max",
  "cpu_cores": 16,
  "memory_total_gb": 128.0,
  "gpu_model": "Apple M3 Max 40-core GPU",
  "whisper_backend": "mlx-whisper",
  "worker_version": "1.0.0",
  "current_job_id": "550e8400-...",
  "current_job_status": "processing"
}
```

`stable_worker_id`: Kurulum sırasında oluşturulan ve `~/.transcription-worker/worker-id` dosyasında saklanan UUID. MAC adresinin aksine VPN, Docker, donanım değişikliklerinden etkilenmez. `mac_address` ikincil olarak saklanır; asıl eşleme `stable_worker_id` üzerindendir.

`current_job_id` + `current_job_status`: İşçi yeniden bağlanıyorsa hâlâ işlediği işi bildirir. Koordinatör grace period sona erene kadar bu işi `pending`'e sıfırlamaz.

**Davranış:**
- Varsa mevcut işçiyi günceller (`stable_worker_id` eşleşmesiyle; `mac_address` geri uyumluluk için), yoksa yeni işçi oluşturur
- `current_job_id` verilmişse:
  - İş DB'de bu işçiye atanmışsa → `status = 'busy'`, iş `processing` olarak korunur
  - İş başkasına atanmışsa → yanıtta `cancel_current_job: true` döner, `status = 'idle'`
  - İş `null` ise → `status = 'idle'`
- `ip_address` güncellenir (DHCP değişikliklerine karşı)

**Yanıt:** `200 OK`

```json
{
  "worker_id": "550e8400-e29b-41d4-a716-446655440010",
  "heartbeat_interval_seconds": 30,
  "coordinator_version": "1.0.0",
  "websocket_url": "ws://192.168.1.101:8080/ws/worker",
  "cancel_current_job": false,
  "recovery_grace_active": true,
  "settings": {
    "whisper_model": "mlx-community/whisper-medium-mlx",
    "whisper_language": "tr",
    "whisper_word_timestamps": true,
    "job_timeout_multiplier": 5
  }
}
```

`cancel_current_job: true` → İşçi, kendi sürecini sonlandırmalı ve `status='idle'` olarak beklemeye geçmeli.  
`recovery_grace_active: true` → Koordinatör yeniden başladı, yeni iş atamıyor; biraz bekle, sonra tekrar talep et.

---

### 5.2 Kalp Atışı

```
POST /api/v1/worker/heartbeat
```

**İstek Gövdesi:**

```json
{
  "worker_id": "550e8400-...",
  "status": "busy",
  "current_job_id": "550e8400-...",
  "job_progress_percent": 42.5,
  "metrics": {
    "cpu_percent": 85.2,
    "memory_used_gb": 78.3,
    "memory_total_gb": 128.0,
    "memory_percent": 61.2,
    "gpu_percent": 92.1,
    "gpu_memory_used_gb": 18.4
  }
}
```

**Yanıt:** `200 OK`

```json
{
  "received_at": "2026-05-18T10:05:30Z",
  "pending_commands": []
}
```

`pending_commands` alanı, WebSocket kanalı kapalıysa kullanılabilecek bekleyen komutları içerir:

```json
{
  "pending_commands": [
    {"command": "PAUSE_JOB", "job_id": "550e8400-..."},
    {"command": "CANCEL_JOB", "job_id": "550e8400-..."}
  ]
}
```

---

### 5.3 Sonraki İşi Talep Et

```
GET /api/v1/worker/jobs/next?worker_id={worker_id}
```

**Davranış:**
- Atomik SELECT + UPDATE (satır düzeyinde kilitlemeli)
- Öncelik DESC, created_at ASC sıralamasıyla ilk `pending` işi alır
- `next_retry_after` gelecekte olan işleri atlar

**Yanıt:** `200 OK` (iş varsa)

```json
{
  "job_id": "550e8400-...",
  "input_path": "Proje_A/toplanti.mp3",
  "original_filename": "toplanti.mp3",
  "relative_folder": "Proje_A",
  "file_size_bytes": 15728640,
  "whisper_settings": {
    "model": "mlx-community/whisper-medium-mlx",
    "language": "tr",
    "word_timestamps": true
  },
  "download_url": "/api/v1/files/550e8400-.../download"
}
```

**Yanıt:** `204 No Content` — Boş kuyruk (işçi bekleme moduna geçmeli)

---

### 5.4 İş Başlatma Bildir

```
POST /api/v1/worker/jobs/{job_id}/start
```

**İstek Gövdesi:**

```json
{
  "worker_id": "550e8400-..."
}
```

**Davranış:** İş durumunu `processing` olarak günceller, `started_at` ayarlar.

**Yanıt:** `200 OK`

---

### 5.5 İlerleme Bildir

```
POST /api/v1/worker/jobs/{job_id}/progress
```

**İstek Gövdesi:**

```json
{
  "worker_id": "550e8400-...",
  "percent": 42.5,
  "elapsed_seconds": 23.4
}
```

**Yanıt:** `200 OK`

```json
{
  "received": true,
  "command": null
}
```

`command` alanı dolu olabilir: `"PAUSE"` | `"CANCEL"` | `null`

---

### 5.6 İş Tamamlama Bildir

```
POST /api/v1/worker/jobs/{job_id}/complete
```

**İstek Gövdesi:** `multipart/form-data`

| Alan | Tür | Açıklama |
|---|---|---|
| `metadata` | JSON string | Tamamlama meta verisi |
| `srt_file` | Dosya | `.srt` çıktı dosyası |
| `json_file` | Dosya | `.json` çıktı dosyası |

`metadata` JSON yapısı:

```json
{
  "worker_id": "550e8400-...",
  "audio_duration_seconds": 3642.5,
  "processing_time_seconds": 1385.2,
  "rtf": 0.38,
  "segment_count": 847,
  "word_count": 6234
}
```

**Davranış:**
1. **İşçi sahipliği doğrulaması** — kritik race condition koruması:
   ```python
   job = await db.get(Job, job_id)
   if job.worker_id != metadata["worker_id"]:
       raise HTTPException(409, "Bu iş artık bu işçiye atanmamış — yükleme reddedildi")
   if job.status not in ("processing", "paused"):
       raise HTTPException(409, f"Geçersiz iş durumu: {job.status} — tamamlama beklenmiyor")
   ```
   Bu kontrol, kalp atışı zaman aşımı sonrası iş başkasına atandığında eski işçinin sonuçlarını ezmesini engeller.
2. **Atomik dosya yazma** — yarım yazılmış dosyaları önlemek için:
   ```python
   tmp_srt  = output_srt_path.with_suffix(".srt.tmp")
   tmp_json = output_json_path.with_suffix(".json.tmp")
   tmp_srt.write_bytes(srt_content)
   tmp_json.write_bytes(json_content)
   # Atomik rename — aynı dosya sisteminde garanti
   tmp_srt.rename(output_srt_path)
   tmp_json.rename(output_json_path)
   ```
3. Çıktı dosyası MD5 karmaları hesaplanır ve `output_srt_hash`, `output_json_hash` sütunlarına yazılır
4. İş durumunu `completed` olarak günceller, çıktı yollarını ve metrikleri kaydeder
5. İşçi istatistiklerini günceller (jobs_completed, total_audio_seconds vb.)

**Yanıt:** `200 OK`

```json
{
  "status": "completed",
  "output_srt_path": "Proje_A/toplanti.srt",
  "output_json_path": "Proje_A/toplanti.json"
}
```

---

### 5.7 İş Hatasını Bildir

```
POST /api/v1/worker/jobs/{job_id}/fail
```

**İstek Gövdesi:**

```json
{
  "worker_id": "550e8400-...",
  "error_message": "mlx-whisper process exited with code -9 (OOM)",
  "error_type": "OOM",
  "error_category": "transient",
  "retry": true
}
```

`error_category` değerleri:
- `"transient"` → Geçici hata; retry uygulanır (OOM, ağ, işçi çökmesi, disk I/O)
- `"deterministic"` → Kalıcı hata; **hemen `failed`**, retry_count ne olursa olsun (bozuk MP3, desteklenmeyen format)
- Belirtilmezse `"transient"` gibi davranılır

**Davranış:**
- `error_category == 'deterministic'` → `status='failed'`, `max_retries=0` olarak sıfırlanır (manuel retry ile override edilebilir)
- `error_category == 'transient'` VE `retry_count < max_retries` → `status='pending'`, `retry_count++`, `next_retry_after` hesaplanır
- `retry_count >= max_retries` → `status='failed'` (kategori fark etmeksizin)

**Yanıt:** `200 OK`

```json
{
  "status": "failed",
  "retry_count": 2,
  "will_retry": true,
  "retry_after": "2026-05-18T10:10:00Z"
}
```

---

## 6. Dosya API'si

### 6.1 MP3 Dosyasını İndir (İşçi Kullanımı)

```
GET /api/v1/files/{job_id}/download
```

**Davranış:**
- İş `worker_id`'nin istek yapan işçiyle eşleştiğini doğrular
- Koordinatörün yerel depolamasından MP3 dosyasını akışla gönderir
- Büyük dosyalar için `Range` isteği destekler (yeniden başlatılabilir indirmeler)

**Yanıt:** `200 OK` veya `206 Partial Content`
- `Content-Type: audio/mpeg`
- `Content-Length: 15728640`
- `Content-Disposition: attachment; filename="toplanti.mp3"`
- `Accept-Ranges: bytes`

---

### 6.2 Çıktı Dosyasını İndir (Dashboard Kullanımı)

```
GET /api/v1/jobs/{job_id}/output/srt
GET /api/v1/jobs/{job_id}/output/json
```

Yalnızca `completed` işler için geçerlidir.

---

## 7. Sistem API'si

### 7.1 Sistem İstatistikleri

```
GET /api/v1/system/stats
```

**Yanıt:** `200 OK`

```json
{
  "jobs": {
    "total": 4521,
    "pending": 234,
    "processing": 3,
    "paused": 1,
    "completed": 4201,
    "failed": 82,
    "cancelled": 0
  },
  "workers": {
    "total": 4,
    "online": 3,
    "offline": 1,
    "busy": 3,
    "idle": 0
  },
  "throughput": {
    "jobs_completed_last_1h": 12,
    "jobs_completed_last_24h": 187,
    "audio_hours_last_24h": 342.5,
    "avg_rtf_last_24h": 0.38
  },
  "coordinator": {
    "version": "1.0.0",
    "uptime_seconds": 86400,
    "db_connected": true,
    "input_dirs_active": 2,
    "storage_used_gb": 45.2,
    "storage_available_gb": 1954.8
  }
}
```

---

### 7.2 Sistem Ayarlarını Getir

```
GET /api/v1/system/settings
```

**Yanıt:** `200 OK`

```json
{
  "worker_heartbeat_timeout_seconds": 90,
  "max_retries_default": 3,
  "retry_delay_seconds": [0, 60, 300],
  "worker_metrics_retention_days": 7,
  "dashboard_refresh_interval_ms": 5000,
  "file_watcher_debounce_seconds": 2,
  "whisper_model": "mlx-community/whisper-medium-mlx",
  "whisper_language": "tr",
  "whisper_word_timestamps": true
}
```

---

### 7.3 Sistem Ayarlarını Güncelle

```
PUT /api/v1/system/settings
```

**İstek Gövdesi:** Kısmi güncelleme desteklenir

```json
{
  "max_retries_default": 5,
  "worker_heartbeat_timeout_seconds": 120
}
```

**Yanıt:** `200 OK` — Güncellenmiş tam ayarlar

---

### 7.4 Dizin Taramasını Tetikle

```
POST /api/v1/scan
```

**İstek Gövdesi:**

```json
{
  "input_directory_id": "550e8400-...",
  "force_reprocess": false
}
```

Tüm dizinler için `input_directory_id` atlanabilir.

**Yanıt:** `202 Accepted`

```json
{
  "scan_id": "550e8400-...",
  "status": "started",
  "message": "Arka planda tarama başlatıldı"
}
```

---

### 7.5 Giriş Dizinlerini Yönet

```
GET    /api/v1/system/input-directories         → Tüm dizinleri listele
POST   /api/v1/system/input-directories         → Yeni dizin ekle
PUT    /api/v1/system/input-directories/{id}    → Dizini güncelle
DELETE /api/v1/system/input-directories/{id}    → Dizini kaldır (işler korunur)
```

**POST İstek Gövdesi:**

```json
{
  "path": "/Volumes/Data/input/Proje_B",
  "output_path": "/Volumes/Data/output/Proje_B",
  "label": "Proje B Toplantıları",
  "watch_recursively": true,
  "default_priority": 5
}
```

---

## 8. WebSocket API

### 8.1 Dashboard WebSocket — `/ws/dashboard`

Dashboard gerçek zamanlı güncellemeler için bu kanala bağlanır.

#### Bağlantı Akışı

```
İstemci → ws://koordinatör:8080/ws/dashboard
Sunucu  → {"type": "connected", "coordinator_version": "1.0.0"}
Sunucu  → {"type": "initial_state", "data": { ... tam sistem durumu ... }}

[Olaylar akışı devam eder]
Sunucu → {"type": "job_created", "data": {...}}
Sunucu → {"type": "job_progress", "data": {...}}
...
```

#### Sunucudan İstemciye Olay Tipleri

**`initial_state`** — Bağlantıdan sonra bir kez gönderilir

```json
{
  "type": "initial_state",
  "data": {
    "jobs": { "pending": 234, "processing": 3, ... },
    "workers": [...],
    "active_jobs": [...]
  }
}
```

**`job_created`**

```json
{
  "type": "job_created",
  "data": {
    "job_id": "550e8400-...",
    "input_path": "Proje_A/toplanti.mp3",
    "status": "pending",
    "created_at": "2026-05-18T10:00:00Z"
  }
}
```

**`job_status_changed`**

```json
{
  "type": "job_status_changed",
  "data": {
    "job_id": "550e8400-...",
    "previous_status": "processing",
    "new_status": "completed",
    "worker_id": "550e8400-...",
    "worker_hostname": "mac-studio-2",
    "timestamp": "2026-05-18T10:25:00Z"
  }
}
```

**`job_progress`**

```json
{
  "type": "job_progress",
  "data": {
    "job_id": "550e8400-...",
    "progress_percent": 42.5,
    "elapsed_seconds": 23.4,
    "worker_id": "550e8400-...",
    "worker_hostname": "mac-studio-2"
  }
}
```

**`worker_status_changed`**

```json
{
  "type": "worker_status_changed",
  "data": {
    "worker_id": "550e8400-...",
    "hostname": "mac-studio-2",
    "previous_status": "busy",
    "new_status": "offline",
    "timestamp": "2026-05-18T10:30:00Z"
  }
}
```

**`worker_metrics`** — Her kalp atışında

```json
{
  "type": "worker_metrics",
  "data": {
    "worker_id": "550e8400-...",
    "hostname": "mac-studio-2",
    "cpu_percent": 85.2,
    "memory_percent": 61.3,
    "gpu_percent": 92.1,
    "current_job_progress": 42.5,
    "timestamp": "2026-05-18T10:05:30Z"
  }
}
```

**`system_alert`** — Önemli sistem olayları

```json
{
  "type": "system_alert",
  "data": {
    "severity": "warning",
    "code": "WORKER_OFFLINE",
    "message": "mac-studio-3 bağlantısı kesildi. Etkilenen 2 iş yeniden kuyruğa alındı.",
    "timestamp": "2026-05-18T10:30:00Z"
  }
}
```

`severity`: `"info"` | `"warning"` | `"error"`

**`heartbeat`** — Bağlantı canlılık teyidi (her 30s)

```json
{
  "type": "heartbeat",
  "data": { "timestamp": "2026-05-18T10:05:30Z" }
}
```

#### İstemciden Sunucuya Mesajlar

**`ping`** — Bağlantı canlılık kontrolü

```json
{"type": "ping"}
```

Yanıt:
```json
{"type": "pong", "timestamp": "2026-05-18T10:05:30Z"}
```

---

### 8.2 İşçi WebSocket — `/ws/worker`

İşçi ajanı bu WebSocket'e bağlanır; koordinatör komutları gerçek zamanlı gönderir.

#### Bağlantı Akışı

```
İşçi    → ws://koordinatör:8080/ws/worker?worker_id={worker_id}
Sunucu  → {"type": "connected", "worker_id": "550e8400-..."}

[Koordinatör komutlar gönderir]
Sunucu → {"type": "PAUSE_JOB", "job_id": "550e8400-..."}
İşçi   → {"type": "PAUSE_ACK", "job_id": "550e8400-..."}

Sunucu → {"type": "RESUME_JOB", "job_id": "550e8400-..."}
İşçi   → {"type": "RESUME_ACK", "job_id": "550e8400-..."}

Sunucu → {"type": "CANCEL_JOB", "job_id": "550e8400-..."}
İşçi   → {"type": "CANCEL_ACK", "job_id": "550e8400-..."}
```

#### Koordinatörden İşçiye Komutlar

| Komut Tipi | Açıklama | İşçi Yanıtı |
|---|---|---|
| `PAUSE_JOB` | Alt sürece SIGSTOP gönder | `PAUSE_ACK` |
| `RESUME_JOB` | Alt sürece SIGCONT gönder | `RESUME_ACK` |
| `CANCEL_JOB` | Alt süreci sonlandır, temizle | `CANCEL_ACK` |
| `UPDATE_SETTINGS` | Ayarları güncelle (sonraki iş için) | `SETTINGS_ACK` |
| `PING` | Bağlantı sağlığı | `PONG` |

#### İşçiden Koordinatöre Mesajlar

İşçi WebSocket üzerinden yalnızca ACK ve canlılık mesajları gönderir; gerçek veriler REST API üzerinden iletilir.

---

## 9. Önemli Uygulama Notları

### 9.1 İş Talep Etme — Race Condition Önleme

```sql
-- Atomik talep; yalnızca tek işçi kazanır.
-- idx_jobs_queue partial index (status='pending') bu sorguda kullanılır.
-- next_retry_after filtresi runtime'da uygulanır (partial index içinde
-- volatile NOW() kullanılamayacağı için).
WITH next_job AS (
    SELECT id FROM jobs
    WHERE status = 'pending'
      AND (next_retry_after IS NULL OR next_retry_after <= NOW())
    ORDER BY priority DESC, created_at ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED  -- Diğer işçilerle yarışmadan atla
)
UPDATE jobs
SET status = 'assigned', worker_id = $1, assigned_at = NOW(), updated_at = NOW()
WHERE id = (SELECT id FROM next_job)
RETURNING *;
```

`FOR UPDATE SKIP LOCKED`, birden fazla işçi aynı anda iş talep ettiğinde veri tutarlılığını garantiler.

### 9.2 Dosya Yolu Güvenliği

Tüm dosya yolları sunulmadan önce doğrulanır:

```python
def safe_join(base: Path, user_path: str) -> Path:
    resolved = (base / user_path).resolve()
    if not str(resolved).startswith(str(base.resolve())):
        raise ValueError("Yol geçişi tespit edildi")
    return resolved
```

### 9.3 İlerleme Tahmini

mlx-whisper gerçek zamanlı ilerleme bildirimi sağlamadığından, işçi şu yaklaşımı kullanır:

```python
# Geçen süreye ve ortalama RTF'ye dayalı tahmini ilerleme
elapsed = time.time() - start_time
estimated_rtf = worker.average_rtf or 0.4  # Varsayılan tahmini RTF
estimated_total = audio_duration * estimated_rtf
estimated_progress = min(95.0, (elapsed / estimated_total) * 100)
```

İlerleme %95'de durdurulur; tamamlandığında %100'e atlar.

---

*Sonraki belge: [WORKER_LIFECYCLE.md](WORKER_LIFECYCLE.md)*
