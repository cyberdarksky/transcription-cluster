# DASHBOARD_SPEC.md
# Dashboard Arayüz Spesifikasyonu

**Teknoloji:** React 18 + TypeScript + Vite + TailwindCSS v4 + shadcn/ui  
**Dil:** Türkçe (tek dil)  
**Varsayılan Tema:** Karanlık mod (Light mode geçiş butonu mevcut)  
**Erişim:** `http://<koordinatör-ip>:8080`

---

## 1. Genel Tasarım Sistemi

### 1.1 Renk Paleti

```
Karanlık Mod (varsayılan):
  --bg-base        : #0A0A0F  (en koyu arka plan)
  --bg-surface     : #111116  (kart/panel arka planı)
  --bg-elevated    : #1A1A22  (yükseltilmiş bileşenler)
  --bg-hover       : #22222C  (hover durumu)
  --border         : #2A2A36
  --text-primary   : #F0F0F5
  --text-secondary : #9090A0
  --text-muted     : #606070

Durum Renkleri (her iki tema için):
  --success : #22C55E  (tamamlandı / çevrimiçi)
  --warning : #F59E0B  (bekliyor / uyarı)
  --danger  : #EF4444  (başarısız / çevrimdışı)
  --info    : #3B82F6  (işleniyor / bilgi)
  --paused  : #A855F7  (duraklatıldı)

Vurgu Rengi:
  --accent  : #6366F1  (indigo — ana eylem rengi)
```

### 1.2 Tipografi

```
Font Ailesi: Inter (Google Fonts, önce yerel; kurulum sırasında paketlenir)
Kod Bloğu: JetBrains Mono (monospaced)

Boyut Sistemi:
  xs: 11px   │ Etiketler, zaman damgaları
  sm: 13px   │ İkincil metin, meta veri
  md: 15px   │ Normal gövde metni
  lg: 17px   │ Bölüm başlıkları
  xl: 20px   │ Ana sayfa başlıkları
  2xl: 24px  │ Büyük sayaçlar / metrikler
  3xl: 32px  │ Hero metrikler
```

### 1.3 Izgara ve Düzen

```
Ana Düzen: Kenar çubuğu (sol, 240px sabit) + Ana içerik alanı
Kenar Boşluğu: 24px
Kart Boşluğu: 16px
Kard İç Boşluk: 20px
Border Radius: 8px (kartlar), 6px (bileşenler), 4px (rozet/etiketler)
```

---

## 2. Sayfa Yapısı

```
/ (Ana Sayfa / Özet)
├── /isler           (İş Listesi)
│   └── /isler/:id   (İş Detayı)
├── /isciler         (İşçi Listesi)
│   └── /isciler/:id (İşçi Detayı)
└── /ayarlar         (Sistem Ayarları)
```

---

## 3. Global Kenar Çubuğu Navigasyonu

```
┌──────────────────────┐
│  🎙 Transkripsiyon   │  ← Logo / Başlık
│     Kümesi           │
├──────────────────────┤
│                      │
│  ◉ Ana Sayfa         │  ← Aktif sayfa vurgusu
│  ☰ İşler             │
│    ■ Bekleyen  (234) │  ← Canlı sayaçlar
│    ▶ İşleniyor  (3) │
│    ✓ Tamamlandı (—)  │
│    ✗ Başarısız  (82) │
│  ⚙ İşçiler     (4)  │
│    ● Çevrimiçi  (3)  │
│    ○ Çevrimdışı (1)  │
│  ⚙ Ayarlar          │
│                      │
├──────────────────────┤
│  Sistem Durumu       │
│  ● Veritabanı: ✓    │
│  ● Dosya İzleyici: ✓│
│  ● mDNS: ✓          │
├──────────────────────┤
│  [☀] / [🌙] Tema    │  ← Karanlık/Açık toggle
└──────────────────────┘
```

Kenar çubuğundaki sayaçlar WebSocket güncellemeleriyle anlık güncellenir.

---

## 4. Ana Sayfa (/)

### 4.1 Üst Satır — Özet Metrikleri (4 Kart)

```
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ Toplam İş       │ │ Aktif İşçiler   │ │ Bugün Tamamlanan│ │ Bugün İşlenen   │
│                 │ │                 │ │                 │ │ Ses              │
│    4,521        │ │    3 / 4        │ │     187         │ │    342.5 saat   │
│                 │ │                 │ │                 │ │                 │
│ ↑ +47 bu hafta  │ │ ● ● ● ○         │ │ Ort. RTF: 0.38  │ │ ≈ 14 gün/ses   │
└─────────────────┘ └─────────────────┘ └─────────────────┘ └─────────────────┘
```

- **Canlı güncelleme:** Tüm kartlar WebSocket üzerinden anlık güncellenir
- **Trend göstergesi:** Haftalık değişim (+/- %)
- **Renk kodları:** Aktif işçi noktaları (yeşil=çevrimiçi, kırmızı=çevrimdışı)

---

### 4.2 Orta Satır — İş Kuyruğu Durumu ve İşçi Aktivitesi

**Sol (2/3 genişlik) — İş Durumu Çubuğu Grafiği:**

```
İş Durumu Dağılımı
┌────────────────────────────────────────────────────────────────────┐
│ ████████████████████████████░░░░░░░░░░▓▓▒░░░░  │
│ ■ Tamamlandı: 4,201  □ Bekleyen: 234  ▓ İşleniyor: 3  ▒ Başarısız: 82 │
└────────────────────────────────────────────────────────────────────┘
```

Saatlik verim çizgi grafiği (son 24 saat):
- X ekseni: Saat (24 nokta)
- Y ekseni: İş/saat
- Çizgi: Tamamlanan işler
- Gölge alanı: İşlenen ses saati

**Sağ (1/3 genişlik) — Aktif İşçi Kartları:**

Her çevrimiçi işçi için mini kart:
```
┌────────────────────────┐
│ ● mac-studio-2         │
│  M3 Max · 128GB        │
│                        │
│ CPU [████████░░] 82%   │
│ RAM [██████░░░░] 61%   │
│ GPU [█████████░] 92%   │
│                        │
│ Proje_A/toplanti.mp3  │
│ ████████░░░░░░ 42%     │  ← Canlı ilerleme çubuğu
│ ~ 18 dk kaldı          │  ← RTF'ye dayalı tahmin
│                        │
│ [Duraklat] [Detay →]  │
└────────────────────────┘
```

---

### 4.3 Alt Satır — Son Aktivite ve Sistem Uyarıları

**Son Tamamlanan İşler (son 10):**

```
✓ 10:25  mac-studio-2  Proje_A/toplanti_son.mp3  (1:02:34 ses · 24 dk işleme · RTF 0.38)
✓ 10:18  mac-studio-1  Proje_B/sunum_hazirlik.mp3  (0:15:22 ses · 6 dk işleme · RTF 0.40)
✗ 10:15  mac-studio-3  Klasor_C/bozuk_dosya.mp3   Hata: Ses çözümlenemiyor (3 deneme)
✓ 09:55  mac-studio-2  Proje_A/haftalik_toplanti.mp3 ...
```

**Sistem Uyarıları (varsa):**

```
⚠ mac-studio-4 son 5 dakikadır çevrimdışı. 2 iş yeniden kuyruğa alındı.
⚠ Disk kullanımı %87'ye ulaştı. Çıktı dizinini kontrol edin.
```

---

## 5. İş Listesi (/isler)

### 5.1 Filtre ve Arama Çubuğu

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  [🔍 Dosya adı veya klasör ara...]   [Durum ▼] [İşçi ▼] [Sırala ▼]  [↺ Tara] │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Durum Filtresi:** Tümü | Bekleyen | İşleniyor | Duraklatıldı | Tamamlandı | Başarısız | İptal

**Sıralama:** Oluşturma (Yeni) | Oluşturma (Eski) | Öncelik | Tamamlanma Tarihi

### 5.2 İş Tablosu

```
┌───┬──────────────────────────────┬──────────────┬───────────┬────────────┬──────────┬─────────────┐
│   │ Dosya                        │ İşçi         │ Durum     │ İlerleme   │ Süre     │ İşlemler    │
├───┼──────────────────────────────┼──────────────┼───────────┼────────────┼──────────┼─────────────┤
│ □ │ 📁 Proje_A                   │              │           │            │          │             │
│   │   toplanti_2024.mp3          │ mac-studio-2 │ ▶ İşleniyor│ ████░ 42% │ 23 dk    │ [⏸][✕]    │
│   │   sunum_hazirlik.mp3         │ mac-studio-1 │ ▶ İşleniyor│ ██░░░ 18% │ 8 dk     │ [⏸][✕]    │
│   │   haftalik_rapor.mp3         │ —            │ ○ Bekliyor│ ———        │ —        │ [✕]        │
│   │                              │              │           │            │          │             │
│ □ │ 📁 Proje_B                   │              │           │            │          │             │
│   │   board_meeting.mp3          │ mac-studio-2 │ ✓ Tamamlandı│ ████ 100%│ 1:02:34 │ [↓SRT][↓JSON]│
│   │   bozuk_dosya.mp3            │ —            │ ✗ Başarısız│ ███░░ 67% │ 15 dk   │ [↺ Yeniden] │
└───┴──────────────────────────────┴──────────────┴───────────┴────────────┴──────────┴─────────────┘
```

**Satır Renklendirmesi:**
- İşleniyor: Sol tarafta mavi kenar çizgisi + hafif mavi arka plan
- Başarısız: Kırmızı kenar çizgisi
- Duraklatıldı: Mor kenar çizgisi
- Tamamlandı: Normal (vurgusuz)

**Toplu İşlem Seçeneği:**
- Birden fazla iş seçilerek yeniden kuyruğa alma, iptal etme veya silme
- Alt çubuğu: "5 iş seçildi — [Yeniden Kuyruğa Al] [İptal Et]"

**Sayfalama:** Alt kısımda sayfa navigasyonu; "50 / 234 iş gösteriliyor"

---

## 6. İş Detayı (/isler/:id)

### 6.1 Üst Bilgi

```
← Geri  |  Proje_A / toplanti_2024.mp3

Durum: ▶ İşleniyor  |  İşçi: mac-studio-2  |  Öncelik: 0  |  Deneme: 1/3

                    [⏸ Duraklat]  [✕ İptal]
```

### 6.2 İlerleme Bloğu (yalnızca `processing` ve `paused` durumlar)

```
┌─────────────────────────────────────────────────────────────────────┐
│  İşleme İlerlemesi                                                  │
│                                                                     │
│  ████████████████████████████░░░░░░░░░░░░░░░░░  42.5%              │
│                                                                     │
│  Geçen süre: 23 dk 14 sn     Tahmini kalan: ~18 dk                 │
│  Ses süresi: 1:02:34 (toplam)                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 6.3 Detay Grid (2 Sütun)

**Sol Sütun — Dosya Bilgisi:**
```
Dosya Adı       : toplanti_2024.mp3
Klasör          : Proje_A
Dosya Boyutu    : 45.2 MB
Ses Süresi      : 1:02:34
Dosya Karması   : a1b2c3d4...
Oluşturma       : 18 May 2026, 10:00
Atanma          : 18 May 2026, 10:01
Başlangıç       : 18 May 2026, 10:01
```

**Sağ Sütun — İşleme Sonucu (tamamlandıysa):**
```
İşçi            : mac-studio-2
İşleme Süresi   : 24 dk 10 sn
RTF             : 0.38x
Segment Sayısı  : 847
Sözcük Sayısı   : 6,234
Tamamlanma      : 18 May 2026, 10:25

Çıktılar:
  [⬇ SRT İndir]  [⬇ JSON İndir]
```

### 6.4 Olay Zaman Çizelgesi

```
Olay Geçmişi
────────────────────────────────────────────────────────
10:01:05   ▶ İşleme Başladı          mac-studio-2
10:00:30   → Atandı                   mac-studio-2
10:00:00   + Oluşturuldu              Dosya izleyici
```

**İlerleme olayları zaman çizelgesinde gösterilmez** (ayrı `progress` filtresi).

### 6.5 Hata Detayı (başarısız işler için)

```
┌─────────────────────────────────────────────────────────────────────┐
│ ✗ Hata Detayı                              [↺ Yeniden Dene]        │
│                                                                     │
│ Hata Türü: TRANSCRIPTION_ERROR                                      │
│ Deneme: 3/3 (Maksimum denemeye ulaşıldı)                            │
│                                                                     │
│ Son Hata Mesajı:                                                    │
│ mlx-whisper process exited with code -9 (OOM)                       │
│ Process killed after using 126.3 GB memory                         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 7. İşçi Listesi (/isciler)

### 7.1 İşçi Kartları Izgarası

3 sütunlu kart görünümü (ekran genişliğine göre 1-3 sütun):

```
┌─────────────────────────────────┐  ┌─────────────────────────────────┐
│ ● mac-studio-1           [⏸]   │  │ ● mac-studio-2           [⏸]   │
│ 192.168.1.101                   │  │ 192.168.1.102                   │
│ Apple M3 Max · 128 GB           │  │ Apple M3 Max · 128 GB           │
│                                 │  │                                 │
│ Durum: İşleniyor                │  │ Durum: Boş                      │
│ ─────────────────────────────── │  │ ─────────────────────────────── │
│ CPU ████████░░░░░░ 62%  (canlı) │  │ CPU ████░░░░░░░░ 28%            │
│ RAM ██████░░░░░░░ 55%           │  │ RAM ████████░░░░ 67%            │
│ GPU █████████░░░░ 87%           │  │ GPU ██░░░░░░░░░░ 15%            │
│                                 │  │                                 │
│ Mevcut İş:                      │  │ Son İş: 10:18 tamamlandı        │
│ Proje_A/toplanti.mp3            │  │ sunum_hazirlik.mp3              │
│ ████████░░░░░ 62%               │  │                                 │
│                                 │  │ ─────────────────────────────── │
│ ─────────────────────────────── │  │ Tamamlanan  : 156               │
│ Tamamlanan  : 234               │  │ Başarısız   : 1                 │
│ Başarısız   : 3                 │  │ Toplam Ses  : 89.3 saat         │
│ Toplam Ses  : 156.4 saat        │  │ Ort. RTF    : 0.41              │
│ Ort. RTF    : 0.38              │  │                                 │
│                          [→]   │  │                          [→]   │
└─────────────────────────────────┘  └─────────────────────────────────┘

┌─────────────────────────────────┐  ┌─────────────────────────────────┐
│ ● mac-studio-3           [▶]   │  │ ○ mac-studio-4                  │
│ 192.168.1.103  [Duraklatıldı]   │  │ 192.168.1.104  [Çevrimdışı]     │
│ ...                             │  │ Son görülme: 14 dk önce         │
└─────────────────────────────────┘  └─────────────────────────────────┘
```

**Gerçek Zamanlı Güncelleme:**
- CPU/RAM/GPU çubukları her kalp atışında (30s) yumuşak animasyon ile güncellenir
- İş ilerleme çubuğu her 10 saniyede bir güncellenir
- İşçi durum rozetleri anlık değişir (yeşil nokta = çevrimiçi)

---

## 8. İşçi Detayı (/isciler/:id)

### 8.1 Üst Bilgi

```
← İşçiler  |  mac-studio-2  ● Çevrimiçi - İşleniyor

Apple M3 Max · 16 çekirdek · 128 GB RAM · 40-çekirdek GPU
IP: 192.168.1.102  ·  mlx-whisper 0.4.2  ·  v1.0.0

[⏸ İşçiyi Duraklat]  [İşleri Görüntüle →]
```

### 8.2 Canlı Metrik Grafikleri (Son 1 Saat)

Üç satır grafik:

```
CPU Kullanımı (%)
100 ┤         ╭──╮  ╭────────╮
 75 ┤    ╭────╯  ╰──╯        ╰────
 50 ┤────╯
  0 ┤
    └────────────────────────────► Zaman (son 1 saat)

Bellek Kullanımı (%)
100 ┤              ╭────────────
 75 ┤──────────────╯
 50 ┤
    └────────────────────────────►

GPU Kullanımı (%)
100 ┤         ╭──────────────────
 75 ┤    ╭────╯
 50 ┤────╯
  0 ┤
    └────────────────────────────►
```

Zaman çözünürlüğü seçici: `1dk` | `5dk` | `15dk` | `1sa` | `24sa`

### 8.3 Geçmiş İşler

Son 50 iş tablosu — filtrele, indir bağlantıları dahil.

### 8.4 Performans İstatistikleri

```
┌────────────────────┬────────────────────┬────────────────────┐
│ Toplam Tamamlanan  │ Toplam İşlenen     │ Ortalama RTF       │
│      234           │   156.4 saat       │      0.38          │
│                    │                    │  (gerçek zamandan  │
│                    │                    │   2.6x hızlı)      │
└────────────────────┴────────────────────┴────────────────────┘
┌────────────────────┬────────────────────┐
│ Ort. İşleme Süresi │ Toplam Çalışma     │
│    24 dakika       │   72 saat          │
│  (iş başına)       │   (bu hafta)       │
└────────────────────┴────────────────────┘
```

---

## 9. Ayarlar Sayfası (/ayarlar)

### 9.1 İzlenen Dizinler

```
İzlenen Giriş Dizinleri
─────────────────────────────────────────────────────
  Yol                    Çıktı Dizini        Durum   İşlemler
  /Volumes/Data/input    /Volumes/Data/output  ● Aktif  [Düzenle] [Sil]
  /Users/admin/mp3s      /Users/admin/output   ● Aktif  [Düzenle] [Sil]

  [+ Yeni Dizin Ekle]

  [↺ Tüm Dizinleri Şimdi Tara]
```

### 9.2 İşçi Ayarları

```
İşçi Yapılandırması
─────────────────────────────────────────────────────
  Kalp Atışı Zaman Aşımı    [  90  ] saniye
  Maksimum Yeniden Deneme   [   3  ] deneme
  Yeniden Deneme Gecikmeleri [ 0, 60, 300 ] saniye (virgülle ayrılmış)
```

### 9.3 Transkripsiyon Ayarları

```
Transkripsiyon Yapılandırması
─────────────────────────────────────────────────────
  Whisper Modeli    [mlx-community/whisper-medium-mlx    ▼]
  Dil               [tr - Türkçe                         ▼]
  Kelime Zaman Damgaları  [● Etkin]
```

### 9.4 Sistem Bilgisi (salt okunur)

```
Sistem Bilgisi
─────────────────────────────────────────────────────
  Koordinatör Versiyonu  : 1.0.0
  Çalışma Süresi         : 3 gün 14 saat 22 dakika
  Veritabanı Bağlantısı  : ✓ Bağlı (PostgreSQL 15.3)
  Dosya İzleyici         : ✓ Çalışıyor
  mDNS Servisi           : ✓ Duyuruluyor
  Disk Kullanımı         : 45.2 GB / 2 TB (%2.3)
```

---

## 10. Gerçek Zamanlı Güncellemeler — Uygulama Detayları

### 10.1 WebSocket Bağlantı Yönetimi (React Hook)

```typescript
// src/hooks/useWebSocket.ts
function useDashboardWebSocket() {
  const queryClient = useQueryClient();
  const { addAlert } = useAlertStore();

  useEffect(() => {
    const ws = new WebSocket(`ws://${window.location.host}/ws/dashboard`);

    ws.onopen = () => {
      console.log('Dashboard WebSocket bağlandı');
    };

    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      handleWebSocketMessage(message, queryClient, addAlert);
    };

    ws.onclose = () => {
      // 3 saniye sonra yeniden bağlan
      setTimeout(() => reconnect(), 3000);
    };

    return () => ws.close();
  }, []);
}

function handleWebSocketMessage(message, queryClient, addAlert) {
  switch (message.type) {
    case 'job_created':
      // İş listesi sorgusunu geçersiz kıl
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard-summary'] });
      break;

    case 'job_status_changed':
      // Belirli işi ve listeyi güncelle
      queryClient.setQueryData(['job', message.data.job_id], (old) => ({
        ...old,
        status: message.data.new_status,
      }));
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard-summary'] });
      break;

    case 'job_progress':
      // Yalnızca ilgili iş sorgusunu güncelle (liste invalidasyonu olmadan)
      queryClient.setQueryData(['job', message.data.job_id], (old) => ({
        ...old,
        progress_percent: message.data.progress_percent,
      }));
      break;

    case 'worker_metrics':
      // İşçi metrik deposunu güncelle (sık, hafif güncelleme)
      queryClient.setQueryData(['worker-metrics-live', message.data.worker_id], message.data);
      break;

    case 'worker_status_changed':
      queryClient.invalidateQueries({ queryKey: ['workers'] });
      break;

    case 'system_alert':
      addAlert({
        severity: message.data.severity,
        message: message.data.message,
        timestamp: message.data.timestamp,
      });
      break;
  }
}
```

### 10.2 Animasyonlar

- **İlerleme çubukları:** `transition: width 0.5s ease-out` — ani zıplama yerine yumuşak kayma
- **Durum rozetleri:** `transition: background-color 0.3s` — renk geçişleri
- **Metrik grafikleri:** Son 60 nokta, her yeni nokta eklendikçe X ekseninde kayar
- **Yeni iş satırı:** Listede görünürken `fade-in + slide-down` animasyonu (300ms)
- **Çevrimdışı işçi:** `pulse` animasyonu (2s) ile kırmızı, sonra soluk gri

### 10.3 Bağlantı Durumu Göstergesi

Sağ üst köşede her zaman görünür:

```
● Bağlı — Gerçek zamanlı   (yeşil nokta)
⚡ Yeniden bağlanılıyor... (sarı yanıp sönen)
○ Bağlantı Kesildi          (kırmızı nokta; son güncelleme: 2 dk önce)
```

Bağlantı kesilirse önbelleğe alınmış veriler gösterilmeye devam eder; eski veri uyarısı gösterilir.

---

## 11. Toast Bildirimleri

Sağ alt köşede, 5 saniye sonra otomatik kapanan bildirimleri:

| Olay | Mesaj | Tür |
|---|---|---|
| İş tamamlandı | "Proje_A/toplanti.mp3 tamamlandı ✓ (RTF: 0.38)" | Başarı |
| İş başarısız | "Proje_B/bozuk.mp3 başarısız — Maksimum deneme aşıldı" | Hata |
| İşçi çevrimdışı | "mac-studio-4 bağlantısı kesildi. 2 iş yeniden kuyruğa alındı" | Uyarı |
| İşçi bağlandı | "mac-studio-4 yeniden bağlandı" | Bilgi |
| Disk uyarısı | "Disk doluluk oranı %87 — Çıktı dizinini kontrol edin" | Uyarı |

**Maksimum 4 toast aynı anda gösterilir;** sonrakiler kuyruğa alınır.

---

## 12. Klavye Kısayolları

| Kısayol | Eylem |
|---|---|
| `G H` | Ana Sayfaya git |
| `G İ` | İş listesine git |
| `G W` | İşçi listesine git |
| `G A` | Ayarlara git |
| `/` | Arama çubuğuna odaklan |
| `Esc` | Arama/modal kapat |
| `R` | Mevcut sayfayı yenile |

---

## 13. Boş Durum Tasarımları

**Boş İş Kuyruğu:**
```
     ✓
  Sıra boş!
Tüm işler tamamlandı
veya klasöre dosya bekleniyor.

[↺ Dizini Tara]
```

**Çevrimdışı İşçi:**
```
     ⚡
 mac-studio-4 çevrimdışı
Son görülme: 14 dakika önce
Atanan işler yeniden kuyruğa alındı.

[Durumu Yenile]
```

---

## 14. Mobil / Küçük Ekran Uyumluluğu

Dashboard öncelikli olarak masaüstü tarayıcılar için tasarlanmıştır; ancak temel görüntüleme tablet boyutunda da çalışmalıdır:

- **≥ 1280px:** Tam yan kenar çubuğu + çok sütunlu düzen
- **768–1279px:** Daraltılmış kenar çubuğu (yalnızca ikonlar) + tek sütun içerik
- **< 768px:** Hamburger menü + dikey yığılmış kartlar (yalnızca izleme amaçlı)

Mobil cihazlarda yönetim işlemleri (duraklat/devam/iptal) kasıtlı olarak kısıtlanmaz; ancak UX optimizasyonu yapılmaz.

---

## 15. Erişilebilirlik

- **ARIA etiketleri:** Tüm interaktif bileşenler (`role`, `aria-label`, `aria-live`)
- **Klavye navigasyonu:** Sekme sırası mantıklı akış izler
- **Renk kontrastı:** WCAG AA standartları (karanlık mod dahil)
- **İlerleme çubukları:** `role="progressbar"` + `aria-valuenow`
- **Canlı bölgeler:** `aria-live="polite"` sayaçlar ve durum güncellemeleri için
- **Hata mesajları:** `role="alert"` ile anında ekran okuyucu bildirimi

---

*Sonraki belge: [PACKAGING_STRATEGY.md](PACKAGING_STRATEGY.md)*
