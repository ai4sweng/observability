# Kendi KIO Modülünü Bağlama — Sıfırdan Kapsamlı Rehber

*Dil: **Türkçe** · [English](INTEGRATION.en.md)*

Bu döküman, **bu observability sistemini hiç bilmeyen** ama kendi KIO modülünü
(kendi kod tabanı, muhtemelen başka bir bilgisayarda) yazmış bir yazılımcının,
o modülü AI4SWENG merkezi platformuna bağlaması için gereken **her adımı**
baştan sona anlatır. Önceden OpenTelemetry, Grafana ya da bu repo hakkında bir
şey bilmenize gerek yok.

Baştan sona okuyup uygularsanız, modülünüz merkezi Grafana'da canlı görünür.
Tahmini süre: **15–30 dakika** (ağ hazırsa).

> **Bu döküman kendi kendine yeter.** Ağ ayrıntısına inmeniz gerekirse
> [`NETWORK.md`](NETWORK.md)'ye, resmî/normatif kurallar için
> [`../observability_integration_contract.pdf`](../observability_integration_contract.pdf)'ye
> yönlendirileceksiniz; ama akışı takip etmek için başka dosyaya geçmeniz şart değil.

İçindekiler:
1. [Bu sistem nedir? (5 dakikalık kavram turu)](#1-bu-sistem-nedir-5-dakikalık-kavram-turu)
2. [Büyük resim: neyi değiştireceksiniz, neye asla dokunmayacaksınız](#2-büyük-resim-neyi-değiştireceksiniz-neye-asla-dokunmayacaksınız)
3. [Ön koşullar](#3-ön-koşullar)
4. [Adım adım kurulum](#4-adım-adım-kurulum)
5. [Kodunuzun şekline göre entegrasyon](#5-kodunuzun-şekline-göre-entegrasyon)
6. [7 zorunlu metrik ve kurallar](#6-7-zorunlu-metrik-ve-kurallar)
7. [Ortam değişkenleri — tam referans](#7-ortam-değişkenleri--tam-referans)
8. [Python değilseniz](#8-python-kullanmıyorsanız)
9. [Opsiyonel akışlar](#9-opsiyonel-akışlar-gerekmiyorsa-hiç-dokunmayın)
10. [Sık sorulan sorular (SSS)](#10-sık-sorulan-sorular-sss)
11. [Sorun giderme](#11-sorun-giderme)

---

## 1. Bu sistem nedir? (5 dakikalık kavram turu)

**Observability**, bir yazılımın çalışırken ürettiği ölçümleri (kaç istek işledi,
ne kadar sürdü, hata oldu mu, kaç token harcadı…) merkezî bir yerde toplayıp
grafiklerle izlemektir. Bizim sistemde parçalar şöyle:

```
   SİZİN MAKİNENİZ                        MERKEZ MAKİNE (B)
 ┌────────────────┐   OTLP/gRPC     ┌──────────────────────────────────────┐
 │  KIO modülünüz │  ── :4317 ───▶  │  OTel Collector  (giriş kapısı)      │
 │  + kio_otel.py │   (siz itersiniz)│      │                              │
 └────────────────┘                  │      ├─▶ VictoriaMetrics  (metrik)  │
                                     │      ├─▶ VictoriaLogs     (log)     │
                                     │      └─▶ Tempo            (trace)   │
                                     │              │                      │
                                     │              ▼                      │
                                     │           Grafana  (dashboard'lar)  │
                                     └──────────────────────────────────────┘
```

Bilmeniz gereken üç şey:

1. **Push tabanlıdır.** Veriyi siz merkeze **itersiniz**. Merkez sizden veri
   çekmez, sizi taramaz, size bağlanmaz. Gereken tek ağ izni: sizden merkeze
   doğru, `:4317` portuna **giden** bir bağlantı.
2. **Konum önemsizdir.** KIO'nuz aynı odada da olabilir başka şehirde de —
   collector'a ulaşabildiği sürece fark etmez. Bu yüzden "yerelde test → LAN →
   uzak ağ" geçişi **kod değil, tek bir adres (`OTEL_EXPORTER_OTLP_ENDPOINT`)**
   değişikliğidir.
3. **Standart araç kullanılır.** Telemetriyi **OpenTelemetry (OTel)** denen
   endüstri standardı SDK üretir. Siz sıfırdan bir şey icat etmezsiniz; hazır
   SDK'yı yapılandırıp 7 standart ölçümü yayınlarsınız. Bu repo size bunu tek
   dosyada hazır veriyor: [`reference-client/kio_otel.py`](reference-client/kio_otel.py).

**"Telemetri" üç türden oluşur** (üçü de aynı `:4317`'den gider):
- **Metrikler** — sayısal zaman serileri (istek sayısı, süre, token…). *Zorunlu.*
- **Trace'ler** — bir isteğin adım adım zaman çizelgesi ("işlem sırası"). *Önerilir, kolay.*
- **Loglar** — serbest metin satırları. *Opsiyonel.*

Bu rehber, zorunlu metrikleri + heartbeat'i + basit bir trace'i çalıştırır; gerisi
opsiyoneldir (§9).

---

## 2. Büyük resim: neyi değiştireceksiniz, neye asla dokunmayacaksınız

Yeni gelenlerin en büyük endişesi "ne kadar çok şeyi kendime göre değiştireceğim"
olur. Cevap: **çok az**. Değiştirme yüzeyi bilinçli olarak küçük ve sınırlıdır.

| Ne | Kim sahibi | Siz değiştirir misiniz? |
|---|---|---|
| **Kendi modül kodunuz** (LLM çağrınız, analiziniz, işiniz) | Siz | ✅ Zaten tamamen sizin |
| **Ortam değişkenleri** (endpoint, `kio.id`, sürüm…) | Siz | ✅ Evet — kurulumunuza göre 3–4 satır |
| **Entegrasyon noktası**: işleyicinizi `with kio.request()` ile sarmak | Siz | ✅ Evet — genelde tek yerde, birkaç satır |
| [`reference-client/kio_otel.py`](reference-client/kio_otel.py) | Referans kit | ⛔ Genelde hayır — olduğu gibi kopyalayın |
| **7 zorunlu metriğin ismi/tipi/birimi** | Sözleşme (§6) | ⛔ Hayır — sabittir, dashboard'lar bunlara bağlı |
| **Merkezî collector / veritabanları / Grafana** | Merkez ekip | ⛔ Hayır — dokunmayın, zaten hazır |
| `remote-kio/docker-compose.yml` ve `.env.example` | Simülatör paketi | ⛔ **Kendi kodunuzu bağlıyorsanız KULLANMAYIN** (bkz. not) |

> **Önemli ayrım:** `remote-kio/` klasöründeki `docker-compose.yml` + kök
> `README.md`, **bizim hazır simülatörümüzü** başka bir makinede çalıştırmak
> içindir. Sizin gerçek modülünüz için bunlara ihtiyacınız yok. Siz yalnızca
> **[`reference-client/`](reference-client/)** klasörünü ve bu rehberi
> kullanırsınız. (Bu ayrım, "çok şey değiştirmem gerekecek" hissinin asıl
> kaynağıdır — simülatör paketini gerçek modül sanmak.)

---

## 3. Ön koşullar

Başlamadan önce elinizde olması gerekenler:

1. **Çalışan bir KIO modülü** — LLM çağıran / kod analiz eden / bir iş yapan kendi
   kodunuz. (Bu rehber Python varsayar; başka dil için §8.)
2. **Python 3.9+** ve `pip` (Python modülü için).
3. **Merkez makinenin adresi** — merkez ekipten alın (ör. `192.168.1.50` veya bir
   Tailscale IP'si). Buna `:4317` ekleyerek endpoint'i oluşturacaksınız.
4. **Size atanmış bir `kio.id`** — merkez ekipten alın. **Tüm çalışan KIO'lar
   arasında benzersiz olmalı** (yerel + uzak). Kendiniz uydurmayın; çakışırsa iki
   KIO'nun verisi Grafana'da iç içe girer. (Sözleşme §1.1)
5. *(Opsiyonel)* Kurulum güvenli/uzaksa bir **OTLP Bearer token**; Langfuse akışını
   da kullanacaksanız **Langfuse anahtarları** — ikisi de merkez ekipten.

---

## 4. Adım adım kurulum

### Adım 1 — Kod yazmadan ÖNCE: merkeze erişimi doğrulayın

"No data" sorunlarının çoğu ağ kaynaklıdır ve koda hiç dokunmadan çözülür. Önce
merkeze ulaşabildiğinizi kanıtlayın:

```bash
cd reference-client
pip install -r requirements.txt
OTEL_EXPORTER_OTLP_ENDPOINT=http://<merkez-adres>:4317 python check_connectivity.py
```

Bu script iki şeyi test eder: (1) porta TCP ile ulaşılıyor mu, (2) gerçek bir
`kio.heartbeat` export'u collector tarafından kabul ediliyor mu. `RESULT: PASS`
görene kadar **devam etmeyin** — bu bir ağ/firewall sorunuysa kodda çözemezsiniz.

`FAIL` alırsanız neredeyse her zaman ya merkez makinede **4317 firewall'da
kapalıdır**, ya **aynı ağda değilsinizdir**. Çözümü [`NETWORK.md`](NETWORK.md)'de:
firewall açma komutları (Windows/Linux), doğru adresi bulma, aynı LAN / statik IP /
Tailscale seçenekleri.

### Adım 2 — Bağımlılıkları kurun ve `kio_otel.py`'yi projenize alın

Referans kit üç Python paketine ihtiyaç duyar (metrik + trace exporter'ları):

```bash
pip install opentelemetry-api==1.44.0 opentelemetry-sdk==1.44.0 opentelemetry-exporter-otlp-proto-grpc==1.44.0
```

Sonra tek dosyayı kendi projenize kopyalayın:

```bash
cp reference-client/kio_otel.py <sizin-projeniz>/
```

`kio_otel.py`'yi düzenlemeniz **gerekmez** — olduğu gibi kullanılır. (İçini merak
ederseniz ~250 satır, yorumlu; ama dokunmadan çalışır.)

### Adım 3 — Ortam değişkenlerini ayarlayın

Modülünüz bu değişkenleri ortamdan okuyacak. En azından ikisi zorunlu:

```bash
# ZORUNLU: merkez collector'ın adresi (gRPC, port 4317)
export OTEL_EXPORTER_OTLP_ENDPOINT="http://<merkez-adres>:4317"

# ZORUNLU: kimliğiniz (merkez ekipten aldığınız benzersiz id)
export OTEL_RESOURCE_ATTRIBUTES="service.name=kio1,service.version=1.0.0,kio.id=kio1,deployment.environment=production"
```

`kio1`'i size atanan id ile, `1.0.0`'ı modülünüzün sürümüyle değiştirin. Tam liste
ve alternatif (tek tek `KIO_ID=...` gibi) değişkenler için §7'ye bakın. Windows
PowerShell'de `export` yerine `$env:AD="değer"` kullanılır; container'da ise bunları
normal container env değişkeni olarak verirsiniz.

### Adım 4 — Kendi kodunuza bağlayın (asıl iş, birkaç satır)

Üç şey eklersiniz: **(a)** başlangıçta bir `KIOTelemetry` örneği + heartbeat,
**(b)** her iş biriminizi `with kio.request()` ile sarmak, **(c)** çıkışta flush.

```python
from kio_otel import KIOTelemetry

# (a) Uygulama BAŞLANGICINDA, yalnızca BİR kez:
kio = KIOTelemetry()        # OTEL_* / KIO_* ortam değişkenlerini kendisi okur
kio.start_heartbeat()       # 60 sn'de bir canlılık sinyali (zorunlu, §6)

# (b) Her "istek" / iş birimi için işleyicinizi sarın:
with kio.request(session_id=gelen_id) as req:
    cikti = benim_gercek_islemim(...)          # ← SİZİN kodunuz, aynen kalır
    req.record_tokens(input=girdi_tok, output=cikti_tok)   # gerçek token sayılarınız
    # req.record_cost_usd(0.0012)              # LLM'inizin $ maliyeti varsa

# (c) Süreç bitmeden, MUTLAKA:
kio.shutdown()              # tamponlanan son veriyi gönderir; atlanırsa son veri kaybolur
```

`with` bloğu şunları **otomatik** yapar, siz uğraşmazsınız:
- aktif oturum sayacı (`kio.session.active_count`, +1 / −1),
- istek süresi (`kio.request.duration_ms`),
- başarı/hata sayımı (`kio.request.count`, `status=ok|error`),
- blok içinden bir **exception** çıkarsa: gerçek bir hata olarak kaydı
  (`kio.request.error_count`, hata tipiyle) ve exception'ı olduğu gibi yeniden
  fırlatma — yani telemetri, kodunuzun akışını değiştirmez.

Tek yapmanız gereken, bloğun **içinde** gerçek token sayılarınızı `req` üzerine
bildirmek. LLM kullanmıyorsanız bu satırları atlayın — metrik yine akar.

### Adım 5 — Çalıştırın ve Grafana'da doğrulayın (kabul kapısı)

Modülünüzü başlatın. ~30–60 saniye içinde:

1. **Metrik kapısı:** merkez Grafana → **KIO Detail** dashboard'u → sağ üstteki
   `KIO` açılır menüsünde **sizin `kio.id`'niz görünmeli**; `kio.heartbeat` tikleyip
   panelleriniz dolmaya başlamalı.
2. *(Langfuse kullanıyorsanız)* **Trace kapısı:** Langfuse projenizde en az bir
   trace `kio.id` + `session.id` ile görünmeli.

Bu ikisi görünene kadar KIO **"onboard" sayılmaz** (Sözleşme §1.5). Görünmüyorsa
§11'e / [`NETWORK.md`](NETWORK.md) §6'ya bakın.

---

## 5. Kodunuzun şekline göre entegrasyon

Adım 4'teki üç parça (kur / sar / flush) her mimaride aynıdır; sadece **nereye**
koyduğunuz değişir. En yaygın üç şekil:

### A) Web servisi (FastAPI / Flask / vb.)

Servis sürekli ayakta; `KIOTelemetry` **uygulama başlangıcında bir kez** kurulur
(her istekte değil), her endpoint çağrısı bir `request()` olur, flush ise uygulama
kapanışında yapılır:

```python
from kio_otel import KIOTelemetry
kio = KIOTelemetry()          # modül yüklenirken bir kez
kio.start_heartbeat()

@app.post("/solve")
async def solve(body):
    with kio.request(session_id=body.session_id) as req:
        result = await benim_llm_isim(body)
        req.record_tokens(input=result.in_tok, output=result.out_tok)
        return result

@app.on_event("shutdown")   # Flask'ta atexit.register(kio.shutdown)
def _flush():
    kio.shutdown()
```

### B) CLI / tek seferlik process (her çağrı ayrı süreç)

Süreç kısa ömürlü; kurulum ve flush aynı `main()` içinde olur. **`shutdown()`
burada kritik** — export aralığını beklemeden çıkarsanız son veri gider. Kısa
komutlar için `EXPORT_INTERVAL_MS`'i düşük tutun (ör. 1000).

```python
def main():
    kio = KIOTelemetry()
    kio.start_heartbeat()
    try:
        with kio.request() as req:
            out = isini_yap()
            req.record_tokens(input=..., output=...)
    finally:
        kio.shutdown()        # flush — kısa ömürlü süreçte şart
```

> Gerçek, satır referanslı bir CLI örneği (FocusTracer/KIO2, tam bu modelde):
> [`../kio2-integration/README.md`](../kio2-integration/README.md).

### C) Sürekli worker / kuyruk tüketici

Bir döngü/kuyruktan iş alıyorsanız, her iş bir `request()`'tir:

```python
kio = KIOTelemetry()
kio.start_heartbeat()
try:
    for task in kuyruk:
        with kio.request(session_id=task.session_id) as req:
            out = task_isle(task)
            req.record_tokens(input=..., output=...)
finally:
    kio.shutdown()
```

> **Ortak kural:** `KIOTelemetry()` küresel OTel sağlayıcılarını kurar; **süreç
> başına yalnızca bir örnek** oluşturun ve `start_heartbeat()`'i bir kez çağırın.

### Docker olmadan çalıştırma (alternatifler)

**Bu entegrasyonun hiçbir yerinde Docker zorunlu değildir.** `kio_otel.py` düz
Python'dur; KIO'nuzu zaten nasıl çalıştırıyorsanız öyle çalıştırırsınız. Tek katı
gereksinim: 3 pip paketi kurulu olsun, ortam değişkenleri set edilsin ve `:4317`'ye
giden erişim olsun. Seçenekler:

- **Düz süreç:** env değişkenlerini verip `python your_kio.py`.
- **venv + systemd (Linux, uzun süreli KIO için önerilir):** bir `.service` dosyası
  yazıp `Restart=always` ile Docker'ın `restart: unless-stopped` davranışını
  alırsınız. Örnek unit dosyası İngilizce rehberde:
  [`INTEGRATION.en.md` §6](INTEGRATION.en.md#6-deployment-without-docker).
- **Windows:** terminalde, Görev Zamanlayıcı ile ya da NSSM üzerinden servis olarak.
- **Süreç yöneticileri:** supervisor, pm2, runit vb. — Python girişini env'lerle
  çalıştırmaları yeter.
- **Yine de container istiyorsanız:** Docker/Podman/nerdctl kullanabilirsiniz;
  entegrasyon umursamaz — sadece zorunlu değildir.

---

## 6. 7 zorunlu metrik ve kurallar

`kio_otel.py` bunların hepsini sizin için oluşturur; bu bölüm **ne yayınladığınızı
anlamanız** ve başka dil kullanıyorsanız (§8) elle kurmanız içindir.

### Zorunlu kimlik (Resource attribute'ları)

`OTEL_RESOURCE_ATTRIBUTES` ile verilir (Adım 3). Collector bunları otomatik olarak
metrik etiketlerine çevirir; dashboard'lar bunlara göre filtreler.

| Attribute | Örnek | Not |
|---|---|---|
| `service.name` | `kio1` | Genelde `kio.id` ile aynı |
| `service.version` | `1.0.0` | Her sürümde güncelleyin (kural G5) |
| `kio.id` | `kio1` | **Benzersiz**, merkez ekiple koordine |
| `deployment.environment` | `production` | `production` / `staging` / `development` |
| `llm` *(ops.)* | `claude-sonnet` | Dashboard'ın modele göre dilimlemesi için |
| `task_type` *(ops.)* | `code-analysis` | Serbest metin; sınırlı bir enum tutun (G3) |

### 7 zorunlu metrik (Sözleşme §2.1) — isim/tip/birim sabittir

| Metrik | Tip | Birim | Ek etiket | Açıklama |
|---|---|---|---|---|
| `kio.request.count` | Counter | 1 | `status` (ok/error) | İşlenen istek sayısı |
| `kio.request.duration_ms` | Histogram | ms | — | Uçtan uca gecikme dağılımı |
| `kio.request.error_count` | Counter | 1 | `error_type` | Sınırlı türlerle hatalar |
| `kio.llm.token_count` | Counter | tokens | `direction` (input/output) | LLM token kullanımı |
| `kio.llm.cost_usd` | Counter | USD | — | Tahmini toplam LLM maliyeti |
| `kio.session.active_count` | UpDownCounter | 1 | — | Anlık aktif oturum sayısı |
| `kio.heartbeat` | Counter | 1 | — | 60 sn'de bir artan canlılık sinyali |

> **Heartbeat neden kritik?** 120 saniyeden uzun süre `kio.heartbeat` göndermeyen
> KIO merkezî kayıtta **"stale" (bayat)** işaretlenir — Grafana Alerting'de bir
> alarm ve Overview dashboard'unda görsel bir tablo bunu otomatik gösterir.
> `kio_otel.py`'de `start_heartbeat()` bunu arka planda hallediyor.

### Kurallar (Sözleşme Appendix G1–G7) — özet

- **G1** İsimler `kio.<domain>.<metric>` deseninde ve **kalıcı** (yayınlandıktan sonra değişmez).
- **G2** Her metrik/trace `kio.id` (trace'lerde ayrıca `session.id`) taşır.
- **G3** **Yüksek kardinaliteli etiket yok**: benzersiz id / URL / e-posta etiket
  değeri olmaz; sınırlı enum kullanın. (`session.id` metrik etiketi *değil*, yalnızca
  trace/log metadatasıdır.)
- **G4** **Sır/PII yok**: API anahtarı, parola, e-posta trace'e/etikete sızmaz.
- **G5** Her sürümde `service.version` güncellenir.
- **G6** Zorunlu metrikleri merkez ekip standardize eder.
- **G7** **Zorunlu 7'nin ötesi self-service**: G1–G5'e uyduğu sürece kendi metriğinizi
  ekleyebilirsiniz. `kio_otel.py`'de `kio.meter` ve `kio.base_labels` üzerinden ham
  instrument'lara erişip tanımlarsınız. (Örnek: bu repodaki `kio.llm.tokens_per_second`,
  `kio.llm.energy_joules` hep G7 ile eklendi.)

---

## 7. Ortam değişkenleri — tam referans

`kio_otel.py` ve preflight bu değişkenleri okur. Bir `.env` şablonu:
[`reference-client/.env.example`](reference-client/.env.example).

| Değişken | Zorunlu? | Varsayılan | Açıklama |
|---|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | ✅ | `http://localhost:4317` | Merkez collector, **gRPC port 4317** (4318 değil!). `http://` = insecure (LAN/VPN için uygun), `https://` = TLS. |
| `OTEL_RESOURCE_ATTRIBUTES` | ✅¹ | — | `service.name=…,service.version=…,kio.id=…,deployment.environment=…`. Sözleşme-standardı, **öncelikli** biçim. |
| `KIO_ID` | ✅¹ | `kioX` | `OTEL_RESOURCE_ATTRIBUTES` yoksa kimlik bundan kurulur (kolaylık için). |
| `SERVICE_VERSION` | ⬜ | `0.0.0` | Modül sürümünüz. |
| `KIO_LLM` | ⬜ | — | Kullandığınız model adı (`llm` etiketi). |
| `KIO_TASK_TYPE` | ⬜ | — | Görev tipi (`task_type` etiketi). |
| `DEPLOYMENT_ENVIRONMENT` | ⬜ | `production` | Ortam adı. |
| `OTEL_EXPORTER_OTLP_HEADERS` | ⬜ | — | Auth açıksa: `Authorization=Bearer <token>`. SDK otomatik okur. |
| `EXPORT_INTERVAL_MS` | ⬜ | `5000` | Metrik export sıklığı. Kısa ömürlü CLI için düşürün. |
| `HEARTBEAT_INTERVAL_S` | ⬜ | `60` | Heartbeat sıklığı. **60'ı geçmeyin** (>120 sn sessizlik = stale). |
| `REQUEST_INTERVAL_S` | ⬜ | `3` | Yalnızca `example_kio.py` kullanır; kendi kodunuzda gereksiz. |

¹ `OTEL_RESOURCE_ATTRIBUTES` **veya** `KIO_ID`'den (en az) biri. İkisi de varsa
`OTEL_RESOURCE_ATTRIBUTES` kazanır (sözleşme onu otoriter kabul eder).

---

## 8. Python kullanmıyorsanız

`kio_otel.py` bir kolaylıktır, zorunluluk değil. Sözleşme dil-bağımsızdır ve
OpenTelemetry tüm büyük dillerde (Go, Java, JS/TS, .NET, Rust…) mevcuttur. Başka
bir dilde şunları yaparsınız:

1. OTLP/**gRPC** metric exporter'ı `OTEL_EXPORTER_OTLP_ENDPOINT`'e yönlendirin.
2. `Resource` attribute'larını `OTEL_RESOURCE_ATTRIBUTES`'tan yükleyin (§6).
3. §6'daki **7 zorunlu instrument'ı birebir aynı isim/tip/birimle** oluşturun.
4. `kio.heartbeat`'i 60 sn'de bir artıran bir arka plan görevi çalıştırın.
5. Her istek için: aktif oturum +1/−1, süre, `status=ok|error` sayımı, hatada
   `error_count`.

Çalışan bir Python referans implementasyonu (elle kurulum deseni) sözleşmenin
ekindedir: [`../observability_integration_contract.pdf`](../observability_integration_contract.pdf)
(Appendix — Reference Implementation). `kio_otel.py` da aynı deseni izler; başka
dile çevirirken model olarak kullanabilirsiniz.

---

## 9. Opsiyonel akışlar (gerekmiyorsa hiç dokunmayın)

`kio_otel.py` bilinçli olarak yalnızca zorunlu OTel akışını (metrics + basit trace +
heartbeat) uygular. Aşağıdakiler tamamen opsiyoneldir:

- **Serbest metin loglar → VictoriaLogs.** LLM çıktı özeti / analiz notu gibi
  yapısal olmayan metinler OTLP logs olarak gider. Kurulum deseni:
  `../kio-simulator/kio_simulator.py`'deki logger bloğu.
- **Langfuse (LLM prompt/completion/cost).** OTel'den ayrı, paralel bir HTTPS akışı;
  yalnızca prompt seviyesinde replay/maliyet analizi istiyorsanız gerekir. Anahtarları
  merkez ekipten alın. Desen: `kio_simulator.py` `emit_langfuse_trace()`.
- **Zengin trace'ler (çocuk span'ler / "işlem sırası").** `kio_otel.py` istek başına
  tek kök span üretir. Adım adım şelale (`prepare_prompt → llm_call → postprocess`)
  isterseniz `kio_simulator.py` `emit_trace()` desenine bakın.
- **NATS ile tetiklenme (orkestrasyon katmanı).** Telemetriden **tamamen ayrıdır**;
  yalnızca KIO'nuzun merkezî `POST /workflow/run` ile tetiklenmesini istiyorsanız
  gerekir. Ayrıntı: ana [`../README.md`](../README.md) "Orchestration layer".

---

## 10. Sık sorulan sorular (SSS)

**Modülümü çok mu değiştireceğim?**
Hayır. Kendi kodunuz aynı kalır; eklediğiniz şey 3 parça (§4): başlangıçta bir
nesne, işleyiciyi saran bir `with`, çıkışta bir `shutdown()`. Metrik isimlerine,
collector'a, docker-compose'a **dokunmazsınız**.

**Modülüm Python değil.** Sorun değil — §8. Sözleşme dil-bağımsızdır.

**LLM kullanmıyorum / token sayım yok.** `req.record_tokens(...)` satırlarını
atlayın. İstek/süre/hata/heartbeat metrikleri yine akar; LLM metrikleri sıfır kalır.

**`kio.id` ne olmalı?** Merkez ekipten alın; kendiniz uydurmayın. Benzersiz olmalı;
çakışırsa iki KIO'nun serileri Grafana'da iç içe girer.

**`http://` mü `https://` mi?** Aynı LAN veya Tailscale/VPN içindeyseniz `http://`
(insecure) yeterlidir ve varsayılandır. Trafiği güvenilmeyen bir ağdan
geçireceksiniz/üretimse `https://` + Bearer token; detay [`NETWORK.md`](NETWORK.md) §4.

**Port 4317 mi 4318 mi?** Bu istemci **gRPC** kullanır → **4317**. 4318 HTTP
içindir; oraya bağlanırsanız sessizce "No data" alırsınız.

**Grafana'yı ben mi kuracağım?** Hayır. Grafana, collector, veritabanları merkez
makinede zaten çalışıyor. Siz sadece veri gönderirsiniz; dashboard'lar hazır.

**Süreç kısa ömürlü, heartbeat 60 sn'yi görmeden bitiyor.** Sorun değil — CLI
modelinde her çağrı bir "istek"tir; süreklilik yerine `shutdown()` ile flush
önemlidir (§5-B). Sürekli ayakta bir servisseniz heartbeat normal işler.

**Verilerim ne kadar saklanıyor?** Metrikler/loglar/trace'ler merkezde 30 gün
tutulur (retention). Bu sizin tarafınızı ilgilendirmez.

---

## 11. Sorun giderme

| Belirti | Olası neden | Çözüm |
|---|---|---|
| `check_connectivity.py` **[1/2] FAIL** | Yanlış IP / firewall kapalı / farklı ağ | [`NETWORK.md`](NETWORK.md) §2 (firewall), §2c (adres), §3 (ağ/VPN) |
| **[1/2] OK ama [2/2] FAIL** | Port açık, collector export'u reddediyor (ör. TLS/auth) | [`NETWORK.md`](NETWORK.md) §4; endpoint şemasını (`http`/`https`) kontrol et |
| Test PASS ama Grafana'da "No data" | Endpoint `:4318`'e (HTTP) ayarlı, istemci gRPC | Portu **4317** yap |
| KIO görünüyor ama seri karışık | Aynı `kio.id` iki kaynaktan gönderiyor | Benzersiz `kio.id` alın (§3) |
| Kısa süre çalışıp kesiliyor | Kısa ömürlü süreçte `shutdown()` çağrılmıyor | §5-B: `finally: kio.shutdown()` |
| KIO "stale" işaretlendi | Heartbeat >120 sn kesildi (süreç durdu / ağ koptu) | Süreç ayakta ve export ediyor mu doğrula |

Ağ/firewall/port kaynaklı her şey için tek durak → [`NETWORK.md`](NETWORK.md) §6.

---

## İlgili dökümanlar

| Döküman | Ne için |
|---|---|
| [`reference-client/`](reference-client/) | Kopyalanabilir minimal kit: `kio_otel.py`, örnek, preflight |
| [`NETWORK.md`](NETWORK.md) | Ağ / firewall / statik IP / Tailscale (derin dalış) |
| [`README.md`](README.md) | Bizim **simülatörümüzü** uzak makinede çalıştırmak (farklı senaryo) |
| [`../kio2-integration/README.md`](../kio2-integration/README.md) | Gerçek FocusTracer (KIO2) modülüne özel, satır referanslı örnek |
| [`../observability_integration_contract.pdf`](../observability_integration_contract.pdf) | Normatif sözleşme (7 metrik, kurallar, referans kod) |
