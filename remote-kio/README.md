# Uzak KIO Paketi (remote-kio)

Bu klasör, tek bir KIO'yu **ana gözlemlenebilirlik yığınından (Collector, VictoriaMetrics,
VictoriaLogs, Tempo, Grafana) fiziksel olarak ayrı bir makinede** çalıştırmak için
hazırlanmış, kopyalanabilir/bağımsız bir "lego parçası"dır. Ana yığın nerede duruyorsa
orada kalır; sadece bu paket + `kio-simulator/` kaynak kodu uzak makineye taşınır.

## Bu paketi bir KIO sahibine gönderirken (zip'lemeden önce)

`observability/kio-simulator/` ve `observability/remote-kio/` klasörlerini olduğu gibi
zip'leyip göndermeden önce şunları çıkar — `.gitignore` bunları git'ten gizler ama ham
bir zip işlemi `.gitignore`'ı bilmez, hâlâ pakete dahil eder:

- `remote-kio/.env` — varsa sil. `.env.example`'ın aksine bu, gerçek (muhtemelen bu
  makineye özel) bir IP/port ve muhtemelen eski/güncel olmayan bir bağlantı bilgisi
  içerir; alıcıya sizin bir test sunucunuzun adresini sızdırmış olursunuz. Alıcı zaten
  `cp .env.example .env` adımıyla kendi `.env`'ini oluşturacak.
- `kio-simulator/__pycache__/` — varsa sil (derlenmiş `.pyc` dosyaları, gereksiz, platform
  bağımlı olabilir).

Kontrol: paketi açtığınızda `remote-kio/` altında yalnızca `docker-compose.yml`,
`.env.example`, `README.md` olmalı; `kio-simulator/` altında `Dockerfile`, `envelope.py`,
`kio_simulator.py`, `requirements.txt` olmalı — başka hiçbir şey.

## Nasıl çalışır

Mimari zaten push tabanlı (bkz. ana `README.md`): her KIO, verisini OTLP/gRPC ile merkezi
Collector'a **iter**. KIO'nun nerede çalıştığı önemli değil — collector'a ağ üzerinden
erişebildiği sürece, ister aynı makinede ister başka bir kıtada olsun fark etmez. Bu
yüzden "uzaklaştırma" için koda hiç dokunulmuyor, sadece bir ortam değişkeni
(`OTEL_EXPORTER_OTLP_ENDPOINT`) değişiyor.

## Adım adım kurulum

**1) İki klasörü uzak makineye kopyala** (aynı göreli konumda kalmalı, çünkü bu paket
`../kio-simulator`'ı build context olarak kullanır — kod tekrarı yerine tek kaynağı
paylaşır):

```bash
scp -r observability/kio-simulator observability/remote-kio kullanici@uzak-sunucu:~/ai4sweng/
```

**2) Uzak makinede `.env` dosyasını hazırla:**

```bash
cd ~/ai4sweng/remote-kio
cp .env.example .env
```

`.env` içinde en az şunları düzenle:

- `KIO_ID` — **benzersiz olmalı** (aşağıdaki uyarıya bak).
- `KIO_LLM`, `KIO_TASK_TYPE` — bu KIO'nun simüle ettiği model/görev.
- `OTEL_EXPORTER_OTLP_ENDPOINT` — ana makinenin adresi, örn. `http://192.168.1.50:4317`
  (aynı LAN) ya da bir VPN/Tailscale IP'si. **Port 4317** olmalı (gRPC) — 4318 (HTTP)
  değil, kod gRPC exporter kullanıyor.

İki opsiyonel blok daha var (`.env.example`'da yorum satırı olarak hazır, varsayılan
kapalı): `KIO_REAL_KPI_ROLE` (bu KIO D1.1'in altı rolünden birini temsil ediyorsa —
bugfix/nlp-requirements/architecture-to-code/ai-sysdev/green-deploy/adoption) ve
`NATS_ENABLED`/`NATS_URL` (bu KIO'nun merkezi Workflow API'den de görev alması
isteniyorsa — telemetriden tamamen ayrı, opsiyonel bir konu, bkz. ana README'nin
"Orchestration layer" bölümü). İkisi de gerekmiyorsa hiç dokunmayın.

**3) Bağlantıyı doğrula** (KIO'yu başlatmadan önce, ana makinenin 4317 portuna
gerçekten ulaşılabildiğinden emin ol):

```bash
nc -zv <ana-makine-IP> 4317
```

Başarısız olursa: ana makinede güvenlik duvarı/router 4317 portunu açmalı (port
yönlendirme), ya da iki makineyi aynı VPN'e (ör. Tailscale, ZeroTier) almak en basit
ve güvenli çözümdür — bu, herhangi bir portu genel internete açmadan çalışır.

**Tailscale ile "aynı ağ": statik IP gerekmez.** Tailscale'in bütün amacı bu — her
cihaza (NAT/router arkasında olsa bile) kalıcı, değişmeyen bir `100.x.y.z` adresi
(ve MagicDNS ile bir hostname) veriyor; port yönlendirme veya statik/genel bir IP satın
almaya gerek yok. KIO sahibini eklemenin iki yolu var:
- **Tam tailnet üyeliği** (admin panelinden "Invite") — ekip arkadaşınızsa mantıklı,
  ACL'lerle hangi cihazları görebileceğini sınırlayabilirsiniz.
- **Tek cihaz paylaşımı** ("Share" — admin panelinde ana makinenizin yanında) — dış bir
  kişiyle (bir KIO sahibiyle) tailnet'inizin tamamına eklemeden, yalnızca o tek makineyi
  paylaşmanızı sağlar; alıcı kendi Tailscale hesabıyla bir link üzerinden kabul eder,
  yalnızca paylaşılan cihaza erişebilir. Tek seferlik/dış bir alıcı için bu daha az
  yetki verir, muhtemelen aradığınız şey bu.

Her iki durumda da alıcı, sizin makinenizin Tailscale IP'sini (`tailscale ip` komutuyla
görülür, hiç değişmez) `.env`'deki `OTEL_EXPORTER_OTLP_ENDPOINT`'e yazar — statik IP
kurulumuyla uğraşmaya gerek kalmaz.

**4) Çalıştır:**

```bash
docker compose up -d --build
```

**5) Doğrula:** ~30-60 saniye içinde Grafana'daki **KIO Detail** dashboard'unun `KIO`
açılır menüsünde yeni `KIO_ID` görünmelidir (heartbeat + metrik export aralığı kadar
beklemek gerekir). `docker compose logs -f` ile de KIO'nun "online" log satırını
görebilirsin.

## KIO_ID çakışması — önemli

Aynı `kio_id` değeriyle iki kaynak (biri yerel, biri uzak) aynı anda veri gönderirse,
zaman serileri iç içe girer ve dashboard karışık görünür. İki seçenek:

- **Yeni bir ID kullan** (örn. `kio5`) — hiçbir şeyi durdurmana gerek kalmaz, en temiz yol.
- **Var olan bir ID'yi devral** (örn. `kio4`) — bu durumda önce ana makinede o KIO'yu
  durdur: ana `observability/` klasöründe `docker compose stop kio4`, sonra uzak
  paketi başlat.

## Güvenlik notu

Bu paket varsayılan olarak **kimlik doğrulamasız (insecure)** bağlanır — tıpkı yerel
kurulum gibi. Bu, güvenilir bir ağda (aynı LAN veya VPN) yapılan testler için yeterlidir.
Bunu genel internete açmadan önce: ana collector'a bir `bearertokenauth` extension'ı
eklenmeli ve `.env` içindeki `OTEL_EXPORTER_OTLP_HEADERS` satırı açılmalıdır (satır
`.env.example` içinde hazır, sadece yorum satırından çıkarılması ve gerçek token'ın
girilmesi yeterli — kod tarafında ekstra değişiklik gerekmez, OpenTelemetry SDK bu
ortam değişkenini otomatik okur).

## Kaldırma

```bash
docker compose down
```

Ana yığında hiçbir değişiklik gerekmez; bu KIO durunca Grafana'daki `kio_heartbeat`
sinyali 120 saniye sonra kesilir ve KIO "stale" (bayat) hale gelir. Bu artık otomatik
olarak işaretleniyor: `grafana/provisioning/alerting/rules.yml`'deki "Stale KIO" alert
kuralı, o KIO_id için 120s'yi aşan sessizlikte Grafana Alerting'de "Firing" durumuna
geçer; Overview dashboard'undaki "Stale KIO Kontrolü" tablosu da aynı eşiği (60s
turuncu / 120s kırmızı) görsel olarak gösterir — bkz. teknik rapor Bölüm 9.5.
