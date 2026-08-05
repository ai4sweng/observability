# KIO2 (FocusTracer) — Gerçek Modül Entegrasyon Rehberi

Bu döküman, **FocusTracer** (D1.1'de KIO2 = "Bug Locate & Fix / LLM Debugger") ekibine
yöneliktir. Amaç: `kio2` kimliğini, şu ana kadar onun yerine veri üreten simülatörden
(`kio2-sim`) devralıp, gerçek FocusTracer çalışmalarının merkezi observability yığınına
(OTel Collector → VictoriaMetrics/VictoriaLogs/Tempo → Grafana, + Langfuse) bağlanmasını
sağlamak.

Referans dökümanlar:
- `docs/AI4SWENG_Observability_Teknik_Rapor_v1.5.docx` — genel mimari (Bölüm 9.5-9.10: Stale KIO alarmı, NATS orkestrasyon katmanı, D1.1 KPI durumu — artık KIO2/KIO3/KIO4/KIO7'nin tümü, bu devir, dashboard uyarlamaları, pytest test paketi).
- `docs/AI4SWENG_KPI_Metrik_Referansi_v1.3.docx` — D1.1'den gelen gerçek proje KPI'ları (v1.3: KIO7'nin (AI-SysDev) gerçek D1.1 KPI'ları da entegre edildi, kio2-sim/kio3/kio4'e ek olarak — D1.1'de KPI'sı tanımlı dört KIO'nun tümü artık kapsanıyor).
- `kio-simulator/kio_simulator.py` — **çalışan referans implementasyon**. Contract'ın
  gerektirdiği her şeyi zaten uyguluyor (metrics/logs/traces/Langfuse pipeline kurulumu,
  gerçek Ollama çağrısı, gerçek GPU enerjisi). Kod örneklerinin çoğu buradan alınabilir.

## 0) Önce karar: nerede çalışacak?

İki senaryo da destekleniyor, hangisi olduğuna göre tek fark bir ortam değişkeni:

**A) Ayrı makine/ağ (FocusTracer kendi ekibinin ortamında çalışıyor)**
`remote-kio/README.md`'deki desen aynen geçerli: mimari push-tabanlı, KIO nerede
çalıştığı önemli değil, collector'a ağ üzerinden erişebildiği sürece yeter.
- `OTEL_EXPORTER_OTLP_ENDPOINT` = merkezi makinenin adresi, örn. `http://<ana-makine-ip>:4317`
- Başlamadan önce doğrula: `nc -zv <ana-makine-ip> 4317`
- Aynı ağda değilseniz Tailscale/ZeroTier gibi bir VPN en basit çözüm.

**B) Bizim docker-compose ağına katılım**
FocusTracer'ı çalıştıran servis, `observability/docker-compose.yml`'e yeni bir servis
olarak eklenir (kio3/kio4 gibi), `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317`
kullanır — aynı Docker network'te olduğu için servis adıyla erişilir.

Hangisi olursa olsun, kod tarafında **hiçbir instrumentation farkı yok** — tek fark bu
endpoint değişkeni.

## 1) Zorunlu kimlik bilgileri (Contract §1.2)

Her metrik/log/trace'e şu attribute'lar eklenmeli (OTel `Resource` üzerinden, bkz.
`kio_simulator.py` satır ~255):

```python
resource = Resource.create({
    "service.name": "kio2",
    "service.version": "<focustracer sürümü>",
    "kio.id": "kio2",                # SABİT — kio2-sim ile çakışmasın diye zaten
                                      # simülatör "kio2-sim" oldu, kio2 artık sizin.
    "deployment.environment": "production",
    "llm": "<gerçekten kullanılan model, örn. qwen2.5:3b>",
    "task_type": "<komuta göre, aşağıya bakın>",
})
```

`task_type` için öneri: FocusTracer'ın CLI komutlarını (`run`, `slice`, `explain`,
`reverse`, `replay`) doğrudan `task_type` değeri yapın — Contract'ta bu alan serbest
metin, sabit bir enum şartı yok.

## 2) FocusTracer'a özel: nereye ne eklenecek

FocusTracer bizim simülatörden farklı olarak **sürekli döngü halinde çalışan bir servis
değil** — her `focustracer <komut>` çağrısı kısa ömürlü bir process (istisna: `gui`
komutu, FastAPI/Uvicorn ile sürekli ayakta kalıyor). Bu yüzden telemetriyi "bir istek =
bir CLI komutu" olarak modellemek en doğal yol.

### `cli.py` → `main()`
Süreç başında bir kere OTel pipeline'ı kurun (metrics + logs + traces exporter'ları,
`kio_simulator.py`'deki kurulum bloğu — satır ~266-380 — neredeyse aynen kopyalanabilir).
Süreç sonunda (`main()` return etmeden) `MeterProvider`/`LoggerProvider`/`TracerProvider`
üzerinde `force_flush()`/`shutdown()` çağırmayı unutmayın — kısa ömürlü bir process'te
`PeriodicExportingMetricReader`'ın export aralığını beklemeden çıkarsanız son verinizi
kaybedersiniz (5000ms varsayılan `EXPORT_INTERVAL_MS` yerine kısa komutlar için daha kısa
bir aralık + `force_flush()` kombinasyonu düşünün).

Her komut çağrısı için bir kök span (`focustracer.command`, attributes: `command`,
`target_script`) + `kio.request.count`/`kio.request.duration_ms` + hata olursa
`kio.request.error_count` (gerçek exception/traceback ile, rastgele değil — bkz. bizim
tarafta yakın zamanda düzelttiğimiz "gerçek hata ≠ simüle hata" ayrımı, `kio_simulator.py`
`_call_ollama_real()`'daki desen).

### `explain_cmd()` (`cli.py`) / `explain_slice()` (`core/explain.py`) — **gerçek LLM metrikleri buradan**

Bu, gerçek Ollama çağrısının yapıldığı tek yer (`agent.analyze_trace()` →
`OllamaClient.generate()` → `POST /api/generate`). "LLM tarafında alınabilecek
metrikler doğrudan alınsın" isteğinizin karşılığı tam olarak burası:

**Şu an bir veri kaybı var:** `OllamaClient.generate()` (`agent/ollama_client.py:30-72`)
Ollama'nın döndürdüğü `eval_count`, `eval_duration`, `prompt_eval_count` alanlarını
okuyup atıyor, sadece `response` metnini dönüyor. Bu üç alan gerçek tok/s'nin kaynağı —
tıpkı bizim `kio_simulator.py`'deki `_call_ollama_real()`'ın yaptığı gibi. Önerilen küçük
değişiklik (interface'i bozmadan, `BaseAIAgent.generate()` imzasını değiştirmeden):

```python
# ollama_client.py — generate() başarılı olduğunda, mevcut davranışa ek olarak:
self.last_usage = {
    "input_tokens": data.get("prompt_eval_count", 0),
    "output_tokens": data.get("eval_count", 0),
    "duration_s": eval_duration_s,          # data["eval_duration"] / 1e9
    "tokens_per_second": eval_count / eval_duration_s if eval_duration_s else 0.0,
}
```

`explain_cmd()` çağrısından hemen sonra `agent.last_usage` okunup şu metrikler gerçek
değerlerle yayınlanır (isimler bizim tarafla birebir aynı, dashboard'lar direkt çalışır):

- `kio.llm.token_count` (counter; `direction=input|output`)
- `kio.llm.tokens_per_second` (histogram)
- `kio.llm.cost_usd` (yerel Ollama için muhtemelen 0 — API maliyeti yok, karar sizin)
- `kio.llm.energy_joules` — gerçek GPU gücü için `kio_simulator.py`'deki
  `_read_gpu_power_watts()` + `_measure_energy_during()` fonksiyonlarını olduğu gibi
  kopyalayabilirsiniz (NVML veya `tools/power_exporter.py` üzerinden okuyor, ikisi de
  yoksa sessizce tahmini formüle düşüyor — hiçbir zaman çökmüyor).
- `source` etiketi: bizim tarafta olduğu gibi `real` sabit kalabilir (siz zaten her
  zaman gerçek Ollama'ya bağlanıyorsunuz, simülasyon yolu yok) — ama Ollama çağrısı
  gerçekten başarısız olursa (`requests.exceptions.*`) bunu `kio.request.error_count`'a
  **gerçek** `error_type` ile yazın, sahte bir "başarılı" sonuç üretmeyin.

`OpenCodeClient` (diğer agent) için aynı token/timing bilgisi muhtemelen mevcut değil
(CLI tabanlı, farklı bir arayüz) — o zaman sadece wall-clock süresi (`kio.request.duration_ms`)
gerçek olur, tok/s için `None`/atlanabilir alan bırakmak, rastgele sayı uydurmaktan iyidir.

### `slice_trace_cmd()` (`cli.py`) — **WP3 "Dynamic slicing success rate" burada gerçek olabilir**

Şu an bizim tarafta bu KPI tamamen simüle (`random.uniform(0.75, 0.97)`). Ama
`slice_trace()` gerçek bir sonuç döndürüyor — `model, result = slice_trace(...)` sonrası
`slice_result_to_dicts(model, result)` ile üretilen `nodes` listesinin boş olup
olmamasına, ya da `cli.py:1220`'deki mevcut kontrole (`no reads` hatası) bakarak
**gerçek** bir başarı/başarısızlık sinyali üretebilirsiniz:

```python
success = bool(nodes)  # ya da projenizdeki daha anlamlı bir "slice bulundu mu" kriteri
slicing_success_hist.record(1.0 if success else 0.0, labels)  # kio.slicing.success_rate
```

Bu, D1.1'in WP3 görev metriğini (hedef ≥%85) **ilk kez gerçek veriyle** dolduracak —
öncelik verilmesini öneririz.

### fix@1 (KPI/"accuracy") hakkında önemli bir sınır — sizin CLAUDE.md'niz de bunu doğruluyor

Projenizin kendi `CLAUDE.md`'si açıkça şunu söylüyor: *"LLM-based fix generation is out
of KIO2 scope (it belongs to KIO7); the `explain` command stays as a standalone
convenience, and in the KIO2 integration the LLM step is a hand-off to KIO7."*

Yani: FocusTracer bir **açıklama** üretiyor (`explain`), bir **düzeltmeyi uygulayıp test
etmiyor**. fix@1'in tanımı ("ilk önerilen yamanın gerçekten testi geçip geçmediği") bu
yüzden FocusTracer'ın kendisinden gerçek olarak gelemez — bu KIO7'nin işi olacak. Şimdilik
`kio.fix.attempt_count` bizim tarafta placeholder kalmaya devam edecek; KIO2
entegrasyonunda bu metriği FocusTracer'dan **beklemiyoruz**. Karışıklığı önlemek için
bunu ekip içinde netleştirmenizi öneririz.

`kio.bugfix.duration_hours` (KPI 6.1) ve `kio.issue.resolution_hours` (KPI 1.2) için de
benzer bir sınır var: bunlar muhtemelen bir issue/ticket'ın **uçtan uca** çözülme süresini
ölçüyor (D1.1'de proje yönetimi seviyesinde bir KPI), FocusTracer'ın tek bir CLI
komutunun süresinden farklı bir kavram. FocusTracer gerçekçi olarak sağlayabileceği şey:
"teknik analiz süresi" (`run` + `slice` + `explain` toplam wall-clock) — tam KPI'nın
kendisi değil, ona giren bir bileşen. Bunu da aynı isimle yayınlamadan önce ekipçe
netleştirmenizi öneririz; yanlış etiketlenmiş "gerçek KPI" görünümü vermek istemeyiz.

### Traces
Kök span `focustracer.command`, çocuk spanlar komuta göre değişebilir — örn. `explain`
için `slice` → `llm_call` (attributes: `llm.model`, `llm.tokens.input/output`) →
`format_output`. `kio_simulator.py`'deki `emit_trace()` (satır ~418) span'leri nasıl
gerçek zaman damgalarıyla (`start_time`/`end_time`, ekstra `sleep` olmadan) kurduğunu
gösteriyor — aynı desen.

### Logs
Serbest metin (örn. `explain`'in ürettiği açıklama, `slice`'ın kriter özeti) OTLP logs
ile VictoriaLogs'a — `kio_simulator.py`'deki logger kurulumu (satır ~357-367) doğrudan
kopyalanabilir.

### Langfuse (opsiyonel ama önerilir)
`explain` gerçek bir LLM çağrısı olduğu için Langfuse'a da yazmak mantıklı —
`emit_langfuse_trace()` (`kio_simulator.py` satır ~472) aynı `session_id`'yi hem OTel
trace'ine hem Langfuse'a vererek ikisini insan gözüyle eşleştirilebilir kılıyor.
`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_BASE_URL` — ana `docker-compose.yml`
içindeki kio3/kio4 servislerinde kullanılan değerlerle aynı (Langfuse tek proje, tüm
KIO'lar aynı anahtarı paylaşıyor).

## 3) Test / doğrulama

1. `focustracer check-agent --agent ollama --model qwen2.5:3b` — LLM bağlantınız zaten
   çalışıyor olmalı, bu adım değişmiyor.
2. Birkaç `focustracer run` / `slice` / `explain` çalıştırın.
3. Grafana → **KIO Detail** → `KIO` açılır menüsünde `kio2` görünmeli (30-60 saniye
   içinde, export aralığınıza bağlı). `kio2-sim` de hâlâ orada — ikisini yan yana
   karşılaştırabilirsiniz.
4. "LLM" panelinde kullandığınız model adı, "Avg tokens/sec" panelinde gerçek bir sayı,
   "Error rate %" panelinde sadece gerçek hatalar görünmeli.
5. `docker compose logs -f kio2` (veya kendi log çıktınız) ile "online" satırını ve
   hata/başarı loglarını takip edin.

## 4) kio2-sim ne olacak?

Simülatör (`kio2-sim`, `docker-compose.yml`'de) FocusTracer doğrulanana kadar paralel
çalışmaya devam ediyor — karşılaştırma/baseline için. Gerçek modül stabil olduğunda
kaldırmak tek satırlık bir `docker compose stop kio2-sim` (ya da servisi
`docker-compose.yml`'den silmek).

## 5) Not: NATS orkestrasyon katmanı (opsiyonel, sizi etkilemez)

`observability/orchestrator/` altında bir NATS JetStream tabanlı orkestrasyon katmanı
(Workflow API + Session Manager + Planner) kuruldu — `kio3`/`kio4` artık bununla
tetikleniyor (bkz. teknik rapor Bölüm 9.6). Planner'ın statik yönlendirme tablosunda
şu an `kio2`/FocusTracer için bir görev tipi tanımlı değil — CLI tabanlı çalışma modeliniz
(kısa ömürlü process, `focustracer <komut>`) bu tetikleme modeliyle bire bir örtüşmüyor.
İsterseniz ileride `orchestrator/planner.py`'deki `DEFAULT_ROUTING_TABLE`'a bir görev tipi
eklenip Workflow API üzerinden `focustracer` komutlarınızı tetikleyecek bir entegrasyon
değerlendirilebilir; şimdilik zorunlu değil, mevcut CLI akışınız değişmeden kalabilir.
