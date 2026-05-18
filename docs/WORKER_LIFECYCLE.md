# WORKER_LIFECYCLE.md
# İşçi Yaşam Döngüsü Spesifikasyonu

**Hedef Bileşen:** İşçi Ajanı (Python süreci, her Mac Studio'da çalışır)

---

## 1. İşçi Durum Makinesi

```
                              ┌─────────────────────────────────────────────┐
                              │                                             │
                   ┌──────────▼──────────┐                                 │
   [Başlangıç] ──► │     KEŞİF           │                                 │
                   │  (mDNS tarama)      │──── Zaman aşımı (60s) ──►       │
                   └──────────┬──────────┘     Yeniden dene                │
                              │ Koordinatör bulundu                        │
                              ▼                                             │
                   ┌──────────────────────┐                                │
                   │    BAĞLANMA          │                                │
                   │  (HTTP + WebSocket)  │──── Bağlantı başarısız ──►     │
                   └──────────┬───────────┘    Geri çekilme                │
                              │ Bağlandı                                   │
                              ▼                                             │
                   ┌──────────────────────┐                                │
                   │     KAYIT            │                                │
                   │  POST /worker/register│                               │
                   └──────────┬───────────┘                               │
                              │ worker_id alındı                          │
                              ▼                                             │
                   ┌──────────────────────┐                                │
              ┌───►│       BOŞ            │◄─── İş tamamlandı / başarısız │
              │    │  (İş bekliyor)       │                                │
              │    └──────────┬───────────┘                               │
              │               │ İş atandı                                  │
              │               ▼                                             │
              │    ┌──────────────────────┐                                │
              │    │    İNDİRME           │                                │
              │    │  MP3 indiriliyor     │──── Hata ──► [HATA] ──────────►│
              │    └──────────┬───────────┘                               │
              │               │ İndirme tamamlandı                        │
              │               ▼                                             │
              │    ┌──────────────────────┐      ┌──────────────────────┐  │
              │    │    İŞLEME            │◄─────┤   DEVAM              │  │
              │    │  mlx-whisper çalışıyor│      │  (SIGCONT gönderildi)│  │
              │    └──────────┬───────────┘      └──────────────────────┘  │
              │    │  PAUSE komut │──────────────►┌──────────────────────┐  │
              │    │  alındı      │               │   DURAKLAMA          │  │
              │               │                  │  (SIGSTOP gönderildi)│  │
              │               │ Tamamlandı        └──────────────────────┘  │
              │               ▼                                             │
              │    ┌──────────────────────┐                                │
              │    │    YÜKLEME           │                                │
              │    │  SRT+JSON gönderiliyor│──── Hata ──► [HATA] ─────────►│
              │    └──────────┬───────────┘                               │
              │               │ Yükleme başarılı                          │
              └───────────────┘                                            │
                                                                           │
                   ┌──────────────────────┐                                │
                   │   YENİDEN BAĞLANMA   │◄───────────────────────────────┘
                   │  (Bağlantı kesildi)  │
                   └──────────┬───────────┘
                              │ Geri çekilme + yeniden dene
                              └──► [BAĞLANMA]
```

---

## 2. Başlangıç Sırası

### 2.1 Yapılandırma Yükleme

```python
# Öncelik sırası:
# 1. Ortam değişkenleri (COORDINATOR_HOST, COORDINATOR_PORT)
# 2. ~/.transcription-worker/config.json (önceki keşiften önbelleklendi)
# 3. mDNS keşfi (varsayılan)

config = WorkerConfig(
    coordinator_host=os.getenv("COORDINATOR_HOST"),      # İsteğe bağlı geçersiz kılma
    coordinator_port=int(os.getenv("COORDINATOR_PORT", "8080")),
    worker_data_dir=Path.home() / ".transcription-worker",
    temp_dir=Path("/tmp/transcription-jobs"),
    log_level=os.getenv("LOG_LEVEL", "INFO"),
    heartbeat_interval=30,
    reconnect_max_delay=120,
    job_poll_interval=5,
)
```

### 2.2 Kararlı İşçi Kimliği

MAC adresi VPN, Docker ağ arabirimleri veya donanım değişikliğiyle değişebilir. Bunun yerine kurulumda bir kez oluşturulan UUID kullanılır:

```python
STABLE_ID_FILE = Path.home() / ".transcription-worker" / "worker-id"

def get_or_create_stable_worker_id() -> str:
    """
    İlk çalıştırmada UUID oluşturur ve dosyaya yazar.
    Sonraki çalıştırmalarda aynı UUID okunur.
    Bu ID, MAC adresi, IP veya hostname değişse de sabit kalır.
    """
    STABLE_ID_FILE.parent.mkdir(parents=True, exist_ok=True)

    if STABLE_ID_FILE.exists():
        worker_id = STABLE_ID_FILE.read_text().strip()
        if worker_id:
            return worker_id

    worker_id = str(uuid.uuid4())
    STABLE_ID_FILE.write_text(worker_id)
    logger.info(f"Yeni kararlı işçi kimliği oluşturuldu: {worker_id}")
    return worker_id
```

### 2.3 Geçici Dizin Kurulumu

```python
# Yeniden başlatmadan kalan geçici dosyaları temizle
for job_dir in config.temp_dir.glob("*/"):
    shutil.rmtree(job_dir, ignore_errors=True)
config.temp_dir.mkdir(parents=True, exist_ok=True)
```

### 2.4 Donanım Bilgisi Toplama

```python
# Apple Silicon'a özgü donanım bilgisi
hardware_info = {
    "stable_worker_id": get_or_create_stable_worker_id(),  # Kalıcı kimlik
    "hostname": socket.gethostname(),
    "mac_address": get_primary_mac_address(),   # Yedek kimlik (ikincil)
    "ip_address": get_local_ip(),
    "cpu_model": get_cpu_model(),       # "Apple M3 Max" vb.
    "cpu_cores": psutil.cpu_count(),
    "memory_total_gb": psutil.virtual_memory().total / (1024**3),
    "gpu_model": get_gpu_model(),        # sysctl ile
    "whisper_backend": "mlx-whisper",
    "worker_version": __version__,
    # Yeniden bağlanmada mevcut işi bildir (grace period protokolü)
    "current_job_id": worker_state.current_job_id,
    "current_job_status": worker_state.status.value if worker_state.current_job_id else None,
}
```

---

## 3. Koordinatör Keşfi (mDNS)

### 3.1 Keşif Süreci

```python
async def discover_coordinator(timeout_seconds: int = 60) -> Optional[str]:
    """
    mDNS kullanarak koordinatörü keşfeder.
    Başarı durumunda: "http://192.168.1.101:8080" döner
    Başarısızlık durumunda: önbellekten IP okumayı dener, yoksa None döner
    """
    from zeroconf.asyncio import AsyncZeroconf, AsyncServiceBrowser

    SERVICE_TYPE = "_transcription._tcp.local."
    discovered = asyncio.Event()
    coordinator_url = None

    class Listener:
        def add_service(self, zc, type_, name):
            nonlocal coordinator_url
            info = zc.get_service_info(type_, name)
            if info:
                ip = socket.inet_ntoa(info.addresses[0])
                port = info.port
                coordinator_url = f"http://{ip}:{port}"
                discovered.set()

        def remove_service(self, *args): pass
        def update_service(self, *args): pass

    async with AsyncZeroconf() as azc:
        browser = AsyncServiceBrowser(azc.zeroconf, SERVICE_TYPE, Listener())
        try:
            await asyncio.wait_for(discovered.wait(), timeout=timeout_seconds)
            # Başarılı keşfi önbelleğe al
            cache_coordinator_url(coordinator_url)
            return coordinator_url
        except asyncio.TimeoutError:
            # Önbellekten yüklemeyi dene
            return load_cached_coordinator_url()
        finally:
            await browser.async_cancel()
```

### 3.2 Koordinatör URL Önbelleği

```python
CACHE_FILE = Path.home() / ".transcription-worker" / "coordinator.json"

def cache_coordinator_url(url: str):
    CACHE_FILE.parent.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps({
        "url": url,
        "cached_at": datetime.utcnow().isoformat()
    }))

def load_cached_coordinator_url() -> Optional[str]:
    if not CACHE_FILE.exists():
        return None
    data = json.loads(CACHE_FILE.read_text())
    # 7 günden eski önbelleği yoksay
    cached_at = datetime.fromisoformat(data["cached_at"])
    if (datetime.utcnow() - cached_at).days > 7:
        return None
    return data["url"]
```

---

## 4. Bağlantı Yönetimi

### 4.1 REST İstemcisi

```python
class CoordinatorClient:
    def __init__(self, base_url: str, worker_id: Optional[str] = None):
        self.base_url = base_url
        self.worker_id = worker_id
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                connect=5.0,
                read=300.0,   # Büyük dosya indirmeleri için uzun okuma zaman aşımı
                write=60.0,
                pool=5.0,
            ),
            limits=httpx.Limits(
                max_keepalive_connections=5,
                max_connections=10,
            ),
        )

    async def download_file(self, job_id: str, dest_path: Path) -> int:
        """Akışlı indirme; kısmi indirme durumunda kaldığı yerden devam eder."""
        headers = {}
        if dest_path.exists():
            headers["Range"] = f"bytes={dest_path.stat().st_size}-"

        async with self._client.stream(
            "GET", f"/api/v1/files/{job_id}/download", headers=headers
        ) as response:
            mode = "ab" if response.status_code == 206 else "wb"
            with open(dest_path, mode) as f:
                async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):  # 1MB chunks
                    f.write(chunk)
            return dest_path.stat().st_size
```

### 4.2 WebSocket Bağlantısı

```python
class WorkerWebSocket:
    def __init__(self, coordinator_url: str, worker_id: str):
        self.ws_url = coordinator_url.replace("http://", "ws://") + f"/ws/worker?worker_id={worker_id}"
        self._ws = None
        self._command_queue: asyncio.Queue = asyncio.Queue()

    async def connect(self):
        self._ws = await websockets.connect(
            self.ws_url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        )
        asyncio.create_task(self._listen())

    async def _listen(self):
        try:
            async for message in self._ws:
                data = json.loads(message)
                await self._command_queue.put(data)
        except websockets.ConnectionClosed:
            await self._command_queue.put({"type": "DISCONNECTED"})

    async def get_command(self, timeout: float = 1.0) -> Optional[dict]:
        try:
            return await asyncio.wait_for(self._command_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
```

---

## 5. Kalp Atışı Döngüsü

Kalp atışı, iş işlemeyle paralel çalışan bağımsız bir görevdir.

```python
async def heartbeat_loop(
    client: CoordinatorClient,
    worker_id: str,
    worker_state: WorkerState,
    stop_event: asyncio.Event,
):
    INTERVAL = 30  # saniye
    RETRY_INTERVAL = 5  # kalp atışı başarısız olursa

    while not stop_event.is_set():
        try:
            payload = {
                "worker_id": worker_id,
                "status": worker_state.status.value,
                "current_job_id": worker_state.current_job_id,
                "job_progress_percent": worker_state.job_progress,
                "metrics": await collect_system_metrics(),
            }
            response = await client.post("/api/v1/worker/heartbeat", json=payload)
            data = response.json()

            # Kalp atışı yanıtındaki bekleyen komutları işle (WebSocket yedek)
            for cmd in data.get("pending_commands", []):
                await worker_state.command_queue.put(cmd)

            await asyncio.sleep(INTERVAL)

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.warning(f"Kalp atışı başarısız: {e}")
            await asyncio.sleep(RETRY_INTERVAL)
```

---

## 6. İş Çalıştırma Döngüsü

```python
async def job_loop(
    client: CoordinatorClient,
    worker_id: str,
    worker_state: WorkerState,
    stop_event: asyncio.Event,
):
    POLL_INTERVAL = 5  # saniye (kuyruk boşsa)
    POLL_INTERVAL_ACTIVE = 1  # saniye (aktif çalışırken)

    while not stop_event.is_set():
        if worker_state.status == WorkerStatus.PAUSED:
            # İşçi duraklatıldı, iş alma
            await asyncio.sleep(POLL_INTERVAL)
            continue

        job = await claim_next_job(client, worker_id)

        if job is None:
            # Boş kuyruk — bekleme modunda bekle
            await asyncio.sleep(POLL_INTERVAL)
            continue

        # İşi çalıştır
        await run_job(client, worker_id, worker_state, job)
```

---

## 7. İş Yürütme Akışı

```python
async def run_job(
    client: CoordinatorClient,
    worker_id: str,
    worker_state: WorkerState,
    job: JobAssignment,
):
    job_dir = Path(f"/tmp/transcription-jobs/{job.job_id}")
    job_dir.mkdir(parents=True, exist_ok=True)
    input_path = job_dir / "input.mp3"

    try:
        # ── 1. Dosyayı İndir ──────────────────────────────────────────
        worker_state.set_status(WorkerStatus.BUSY, job.job_id)
        logger.info(f"İndiriliyor: {job.input_path}")

        await client.download_file(job.job_id, input_path)

        # ── 2. Başlangıcı Bildir ──────────────────────────────────────
        await client.post(f"/api/v1/worker/jobs/{job.job_id}/start",
                         json={"worker_id": worker_id})

        # ── 3. Transkripsiyon ─────────────────────────────────────────
        audio_duration = get_audio_duration(input_path)
        result = await run_transcription_with_control(
            client=client,
            worker_id=worker_id,
            worker_state=worker_state,
            job_id=job.job_id,
            input_path=input_path,
            audio_duration=audio_duration,
            settings=job.whisper_settings,
        )

        # ── 4. Çıktıları Oluştur ──────────────────────────────────────
        srt_path = job_dir / "output.srt"
        json_path = job_dir / "output.json"

        generate_srt(result.segments, srt_path)
        generate_json(result, job, json_path)

        # ── 5. Sonuçları Yükle ────────────────────────────────────────
        await upload_results(client, worker_id, job, srt_path, json_path, result)

    except JobCancelledException:
        logger.info(f"İş iptal edildi: {job.job_id}")
        # İptal zaten koordinatör tarafından işlendi

    except TranscriptionError as e:
        logger.error(f"Transkripsiyon hatası: {e}")
        await client.post(f"/api/v1/worker/jobs/{job.job_id}/fail", json={
            "worker_id": worker_id,
            "error_message": str(e),
            "error_type": "TRANSCRIPTION_ERROR",
            "retry": True,
        })

    except Exception as e:
        logger.exception(f"Beklenmeyen hata iş {job.job_id} için")
        await client.post(f"/api/v1/worker/jobs/{job.job_id}/fail", json={
            "worker_id": worker_id,
            "error_message": f"{type(e).__name__}: {str(e)}",
            "error_type": "UNKNOWN_ERROR",
            "retry": True,
        })

    finally:
        # Geçici dosyaları temizle
        shutil.rmtree(job_dir, ignore_errors=True)
        worker_state.set_status(WorkerStatus.IDLE, None)
```

---

## 8. Transkripsiyon Süreci ve Duraklatma/Devam

### 8.1 Alt Süreç Tabanlı Transkripsiyon

Gerçek duraklatma/devam (SIGSTOP/SIGCONT) için transkripsiyon ayrı bir alt süreçte çalışır:

```python
async def run_transcription_with_control(
    client, worker_id, worker_state, job_id,
    input_path, audio_duration, settings
) -> TranscriptionResult:
    """
    mlx-whisper'ı ayrı bir alt süreçte çalıştırır.
    SIGSTOP/SIGCONT ile gerçek duraklatma/devam desteklenir.
    """

    # Alt süreç başlatıcı script: stdout'a JSON sonucu yazar
    #
    # MODEL YOLU — settings.model YEREL MUTLAK YOL OLMALIDIR:
    #   Doğru:   "/opt/transcription-models/whisper-medium-mlx"
    #   Yanlış:  "mlx-community/whisper-medium-mlx"  ← internet gerektirir!
    #
    # mlx_whisper.transcribe() path_or_hf_repo parametresini hem HuggingFace
    # repo adı hem de yerel dizin yolu olarak kabul eder. Çevrimdışı çalışma
    # için kurulum sırasında indirilen modelin MUTLAK yolu verilir.
    # HuggingFace önbelleğine sembolik bağlantı oluşturmak GEREKMEZ.
    model_path = settings.model  # = "/opt/transcription-models/whisper-medium-mlx"

    script = f"""
import json, sys
import mlx_whisper

result = mlx_whisper.transcribe(
    "{input_path}",
    path_or_hf_repo="{model_path}",
    language="{settings.language}",
    word_timestamps={str(settings.word_timestamps).lower()},
    verbose=False,
    fp16=False,       # MLX kendi optimizasyonunu yapar
)

# Sonuçları stdout'a yaz
print(json.dumps({{
    "text": result["text"],
    "segments": [
        {{
            "id": seg["id"],
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"],
            "words": [
                {{"word": w["word"], "start": w["start"], "end": w["end"], "probability": w["probability"]}}
                for w in seg.get("words", [])
            ],
            "avg_logprob": seg.get("avg_logprob"),
            "no_speech_prob": seg.get("no_speech_prob"),
        }}
        for seg in result["segments"]
    ],
    "language": result["language"],
}}))
"""

    # Python yorumlayıcısını belirle (işçinin sanal ortamından)
    python_executable = sys.executable

    proc = await asyncio.create_subprocess_exec(
        python_executable, "-c", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Süreç PID'ini proc.pid üzerinden al; create_subprocess_exec döndüğünde PID atanmış olur.
    # Ancak sinyal göndermeden önce sürecin gerçekten başladığını doğrula.
    if proc.pid is None:
        raise TranscriptionError("Alt süreç başlatılamadı — PID atanamadı")
    worker_state.transcription_pid = proc.pid

    start_time = asyncio.get_event_loop().time()
    last_progress_report = 0.0

    # İş zaman aşımı hesaplama
    job_timeout_multiplier = worker_state.config.job_timeout_multiplier or 5
    max_duration = audio_duration * job_timeout_multiplier if audio_duration > 0 else 3600

    # Komutları dinle, ilerleme raporla, zaman aşımı denetle
    while proc.returncode is None:
        # Zaman aşımı kontrolü — mlx-whisper askıda kalırsa işi zorla sonlandır
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed > max_duration:
            logger.error(
                f"İş {job_id} zaman aşımına uğradı ({elapsed:.0f}s > {max_duration:.0f}s). "
                "mlx-whisper zorla sonlandırılıyor."
            )
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise TranscriptionError(
                f"İş zaman aşımı: {elapsed:.0f}s geçti (maksimum: {max_duration:.0f}s)",
                error_category="transient",  # Zaman aşımı geçici hata — yeniden denenebilir
            )

        # Komut kuyruğunu kontrol et
        command = await worker_state.command_queue.get_nowait_safe()

        if command:
            if command["type"] == "PAUSE_JOB":
                # Güvenli sinyal gönderme — SIGSTOP öncesi sürecin hâlâ çalıştığını doğrula
                if proc.returncode is None:
                    try:
                        os.kill(proc.pid, signal.SIGSTOP)
                        worker_state.set_status(WorkerStatus.PAUSED, job_id)
                        logger.info(f"İş {job_id} duraklatıldı (SIGSTOP → pid {proc.pid})")
                    except ProcessLookupError:
                        logger.warning("SIGSTOP gönderilemedi — süreç zaten sonlanmış")
                # DURAKLATILMIŞ modda ilerleme güncellemesi GÖNDERİLMEZ.
                # Donmuş bir sürecin ilerleme tahmini göndermesi yanıltıcıdır.

            elif command["type"] == "RESUME_JOB":
                if proc.returncode is None:
                    try:
                        os.kill(proc.pid, signal.SIGCONT)
                        worker_state.set_status(WorkerStatus.BUSY, job_id)
                        logger.info(f"İş {job_id} devam ettirildi (SIGCONT → pid {proc.pid})")
                    except ProcessLookupError:
                        logger.warning("SIGCONT gönderilemedi — süreç zaten sonlanmış")

            elif command["type"] in ("CANCEL_JOB", "DISCONNECTED"):
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                raise JobCancelledException(f"Komut nedeniyle iptal: {command['type']}")

        # İlerleme tahmini güncelle — YALNIZCA aktif işleme sırasında raporla
        # DURAKLATILMIŞ durumda ilerleme tahmini gönderilmez (SIGSTOP altında gerçek
        # ilerleme olmuyor; tahmin güncellense de API'ye gönderilmez).
        if worker_state.status == WorkerStatus.BUSY:
            avg_rtf = worker_state.average_rtf or 0.40
            estimated_total = audio_duration * avg_rtf if audio_duration > 0 else 1
            progress = min(95.0, (elapsed / estimated_total) * 100)
            worker_state.job_progress = progress

            # Her 10 saniyede bir raporla (float karşılaştırması yerine sayaç)
            if elapsed - last_progress_report >= 10.0:
                last_progress_report = elapsed
                try:
                    await client.post(
                        f"/api/v1/worker/jobs/{job_id}/progress",
                        json={"worker_id": worker_id, "percent": round(progress, 1),
                              "elapsed_seconds": round(elapsed, 1)},
                    )
                except Exception:
                    pass  # İlerleme raporu kritik değil; kalp atışı yedek

        await asyncio.sleep(0.5)

    # Süreci tamamlandı
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        exit_code = proc.returncode
        # -9 (SIGKILL) genellikle OOM → geçici; diğerleri belirleyici olabilir
        category = "transient" if exit_code in (-9, -11) else "deterministic"
        raise TranscriptionError(
            f"mlx-whisper başarısız oldu (kod {exit_code}): {stderr.decode()[:500]}",
            error_category=category,
        )

    result_data = json.loads(stdout.decode())
    worker_state.transcription_pid = None
    return TranscriptionResult(**result_data)
```

### 8.2 SIGSTOP / SIGCONT Davranışı

macOS üzerinde SIGSTOP/SIGCONT davranışı:

| Sinyal | Etki | Metal GPU |
|---|---|---|
| `SIGSTOP` | Süreç askıya alınır; CPU/GPU kullanımı sıfıra iner | Metal GPU da askıya alınır |
| `SIGCONT` | Süreç tam kaldığı yerden devam eder | Bellekteki tüm tensörler korunur |

**Önemli:** SIGSTOP ve SIGCONT, tüm sürecin bellek durumunu ve Metal GPU bağlamını koruduğundan, mlx-whisper'ın durum kaybı yaşamadan devam etmesi garanti altındadır.

---

## 9. SRT Oluşturma

```python
def generate_srt(segments: list[Segment], output_path: Path) -> None:
    """
    Whisper segment listesinden SRT dosyası oluşturur.
    """
    lines = []

    for i, seg in enumerate(segments, start=1):
        start_ts = format_srt_timestamp(seg.start)
        end_ts = format_srt_timestamp(seg.end)

        lines.append(str(i))
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(seg.text.strip())
        lines.append("")  # Boş satır ayırıcı

    # UTF-8 ile yaz (Türkçe karakter desteği için zorunlu)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def format_srt_timestamp(seconds: float) -> str:
    """
    3665.123 → "01:01:05,123"
    SRT formatı: HH:MM:SS,mmm
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
```

**Örnek SRT Çıktısı:**
```
1
00:00:00,000 --> 00:00:03,240
Merhaba, bugünkü toplantıya hoş geldiniz.

2
00:00:03,240 --> 00:00:07,890
Gündemin ilk maddesini ele alarak başlayalım.

3
00:00:07,890 --> 00:00:12,450
Bu çeyrek dönem performans değerlendirmesini yapacağız.
```

---

## 10. JSON Çıktı Formatı

```python
def generate_json(
    result: TranscriptionResult,
    job: JobAssignment,
    output_path: Path,
    processing_metadata: dict,
) -> None:
    output = {
        "version": "1.0",
        "file": {
            "name": job.original_filename,
            "path": job.input_path,
            "folder": job.relative_folder,
        },
        "transcription": {
            "language": result.language,
            "model": job.whisper_settings.model,
            "text": result.text,
            "segment_count": len(result.segments),
            "word_count": sum(len(s.text.split()) for s in result.segments),
        },
        "segments": [
            {
                "id": seg.id,
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "duration": round(seg.end - seg.start, 3),
                "text": seg.text.strip(),
                "avg_logprob": round(seg.avg_logprob, 4) if seg.avg_logprob else None,
                "no_speech_prob": round(seg.no_speech_prob, 4) if seg.no_speech_prob else None,
                "words": [
                    {
                        "word": w.word,
                        "start": round(w.start, 3),
                        "end": round(w.end, 3),
                        "probability": round(w.probability, 4),
                    }
                    for w in seg.words
                ] if seg.words else [],
            }
            for seg in result.segments
        ],
        "metadata": {
            "transcribed_at": datetime.utcnow().isoformat() + "Z",
            "worker_id": job.worker_id,
            "worker_hostname": processing_metadata["worker_hostname"],
            "audio_duration_seconds": round(processing_metadata["audio_duration"], 3),
            "processing_time_seconds": round(processing_metadata["processing_time"], 3),
            "real_time_factor": round(
                processing_metadata["processing_time"] / processing_metadata["audio_duration"], 4
            ) if processing_metadata["audio_duration"] > 0 else None,
        },
    }

    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
```

---

## 11. Yeniden Bağlanma Mantığı

### 11.1 Geri Çekilme Stratejisi

```python
class ReconnectManager:
    BASE_DELAY = 5       # saniye
    MAX_DELAY = 120      # saniye
    MULTIPLIER = 2.0
    JITTER_FACTOR = 0.2  # ±%20 rastgele jitter

    def __init__(self):
        self._attempt = 0
        self._current_job: Optional[JobAssignment] = None

    def save_current_job(self, job: Optional[JobAssignment]):
        """Yeniden bağlandığında bildirmek üzere mevcut işi sakla."""
        self._current_job = job

    async def reconnect_loop(
        self,
        coordinator_url: str,
        client: CoordinatorClient,
        worker_state: WorkerState,
    ) -> bool:
        """
        Koordinatöre yeniden bağlanmayı dener.
        Başarılı olursa True, vazgeçilirse False döner.
        """
        while True:
            self._attempt += 1
            delay = min(
                self.BASE_DELAY * (self.MULTIPLIER ** (self._attempt - 1)),
                self.MAX_DELAY
            )
            jitter = delay * self.JITTER_FACTOR * (random.random() * 2 - 1)
            actual_delay = delay + jitter

            logger.info(f"Yeniden bağlanmaya çalışılıyor ({self._attempt}). "
                       f"{actual_delay:.1f}s bekleniyor...")
            await asyncio.sleep(actual_delay)

            try:
                # Koordinatör erişilebilir mi?
                resp = await client.get("/api/v1/system/stats", timeout=5.0)
                if resp.status_code == 200:
                    # Başarılı! Yeniden kayıt ol
                    await self._reregister(client, worker_state)
                    self._attempt = 0
                    return True
            except (httpx.ConnectError, httpx.TimeoutException):
                logger.debug(f"Koordinatöre ulaşılamıyor (deneme {self._attempt})")

    async def _reregister(self, client: CoordinatorClient, worker_state: WorkerState):
        """Yeniden bağlantı sonrası kayıt ve durum senkronizasyonu."""
        response = await client.post("/api/v1/worker/register", json=get_hardware_info())
        data = response.json()
        worker_id = data["worker_id"]

        # Eğer işleme devam ediliyorsa, koordinatöre bildir
        if (self._current_job and
            worker_state.status == WorkerStatus.BUSY and
            worker_state.transcription_pid):

            # Koordinatörün işi başka birine atayıp atamadığını kontrol et
            job_status = await client.get(
                f"/api/v1/jobs/{self._current_job.job_id}"
            )
            if job_status.json()["worker_id"] == worker_id:
                # İş hâlâ bu işçiye ait; devam et
                logger.info(f"Mevcut iş yeniden bağlantıdan sonra devam ediyor: "
                           f"{self._current_job.job_id}")
            else:
                # İş yeniden atandı; mevcut işlemeyi iptal et
                logger.warning("İş yeniden atandı; mevcut transkripsiyon iptal ediliyor")
                if worker_state.transcription_pid:
                    os.kill(worker_state.transcription_pid, signal.SIGTERM)
                worker_state.set_status(WorkerStatus.IDLE, None)
                self._current_job = None
```

### 11.2 Yeniden Bağlanma Senaryosu Akışları

**Senaryo A: Geçici Ağ Kesintisi (< 90 saniye)**

```
İşçi işliyor
    ↓
Ağ kesintisi — kalp atışı başarısız
    ↓
İşçi yeniden bağlanma döngüsüne girer (işleme devam eder)
    ↓
30 saniye sonra bağlantı geri gelir
    ↓
İşçi yeniden kayıt olur
    ↓
Koordinatör: kalp atışı zaman aşımı henüz dolmadı (90s)
    ↓
İş atanmış olarak kalır, işçi işlemeye devam eder
    ↓
İşçi tamamlamayı raporlar → İş tamamlandı ✓
```

**Senaryo B: Uzun Ağ Kesintisi (> 90 saniye)**

```
İşçi işliyor
    ↓
Uzun ağ kesintisi
    ↓
Koordinatör: kalp atışı zaman aşımı (90s) doldu
    ↓
Koordinatör: işçiyi 'offline' olarak işaretler
    ↓
Koordinatör: iş yeniden 'pending' olarak kuyruğa alınır
    ↓
Başka bir işçi veya aynı işçi (yeniden bağlandığında) işi alır
    ↓
[Ağ geri geldi]
    ↓
İşçi yeniden kayıt olur
    ↓
İşçi: Çalışan transkripsiyon sürecim var ama koordinatör işi başkasına atadı
    ↓
İşçi: Kendi sürecini sonlandırır (SIGTERM)
    ↓
İşçi: BOŞ durumuna geçer, yeni iş bekler ✓
```

**Senaryo C: İşçi Çökmesi ve Yeniden Başlatma**

```
İşçi süreci çöküyor (OOM, kaza vb.)
    ↓
launchd: İşçi sürecini yeniden başlatır (5 saniye sonra)
    ↓
İşçi: /tmp/transcription-jobs/ temizler
    ↓
İşçi: mDNS keşfi veya önbellekten koordinatöre bağlanır
    ↓
İşçi: Yeniden kayıt olur (status → idle)
    ↓
Koordinatör: kalp atışı zaman aşımıyla işi zaten yeniden kuyruğa almıştır
    ↓
İşçi: Yeni iş alır ✓
```

---

## 12. Sistem Metriği Toplama

```python
async def collect_system_metrics() -> dict:
    """
    psutil ve Apple'a özgü API'ler kullanarak sistem metriklerini toplar.
    """
    # CPU ve Bellek (tüm platformlarda)
    cpu_percent = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()

    metrics = {
        "cpu_percent": round(cpu_percent, 1),
        "memory_used_gb": round(mem.used / (1024**3), 2),
        "memory_total_gb": round(mem.total / (1024**3), 2),
        "memory_percent": round(mem.percent, 1),
        "gpu_percent": None,
        "gpu_memory_used_gb": None,
    }

    # GPU Metrikleri — Apple Silicon (powermetrics veya osascript)
    try:
        gpu_metrics = await get_apple_gpu_metrics()
        metrics.update(gpu_metrics)
    except Exception:
        pass  # GPU metrikleri isteğe bağlı

    return metrics


async def get_apple_gpu_metrics() -> dict:
    """
    Apple Silicon GPU kullanımını `ioreg` ile alır.
    sudo gerektirmez.
    """
    proc = await asyncio.create_subprocess_exec(
        "ioreg", "-r", "-d", "1", "-w", "0", "-n", "IOGPU",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
    output = stdout.decode()

    gpu_percent = None
    # Çıktıdan Device Utilization % değerini parse et
    for line in output.splitlines():
        if "Device Utilization" in line:
            match = re.search(r'(\d+(?:\.\d+)?)', line)
            if match:
                gpu_percent = float(match.group(1))
                break

    return {"gpu_percent": gpu_percent}
```

---

## 13. Graceful Shutdown (Temiz Kapatma)

```python
async def graceful_shutdown(
    worker_state: WorkerState,
    client: CoordinatorClient,
    stop_event: asyncio.Event,
):
    """
    SIGTERM alındığında çağrılır.
    Mevcut iş varsa koordinatöre bildirir ve geçici dosyaları temizler.
    """
    logger.info("Kapatma sinyali alındı. Temizleniyor...")
    stop_event.set()

    if worker_state.current_job_id and worker_state.transcription_pid:
        logger.info(f"Transkripsiyon süreci sonlandırılıyor: {worker_state.transcription_pid}")
        try:
            os.kill(worker_state.transcription_pid, signal.SIGTERM)
            await asyncio.sleep(2.0)
            # Hâlâ çalışıyorsa zorla sonlandır
            if worker_state.is_pid_alive(worker_state.transcription_pid):
                os.kill(worker_state.transcription_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

        # Koordinatöre işin yeniden kuyruğa alınması için haber ver
        try:
            await client.post(f"/api/v1/worker/jobs/{worker_state.current_job_id}/fail",
                json={
                    "worker_id": worker_state.worker_id,
                    "error_message": "İşçi temiz kapatma ile durduruldu",
                    "error_type": "WORKER_SHUTDOWN",
                    "retry": True,
                })
        except Exception:
            pass  # En iyi çaba; koordinatör zaten zaman aşımı ile kurtarır

    # Geçici dizini temizle
    shutil.rmtree("/tmp/transcription-jobs", ignore_errors=True)
    logger.info("İşçi temiz şekilde kapatıldı.")
```

---

*Sonraki belge: [DASHBOARD_SPEC.md](DASHBOARD_SPEC.md)*
