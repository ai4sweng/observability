# Ağ Kurulumu — Uzak bir KIO'yu merkezi platforma bağlama

Bu döküman, **başka bir makinede çalışan bir KIO'nun** merkezi observability
yığınına (OTel Collector → VictoriaMetrics/VictoriaLogs/Tempo → Grafana) ağ
üzerinden nasıl ulaşacağını anlatır. Hem "kendi kodunu bağlayan" (bkz.
[`INTEGRATION.md`](INTEGRATION.md)) hem de "bizim simülatörü uzakta çalıştıran"
(bkz. [`README.md`](README.md)) senaryolar aynı ağ modelini kullanır — fark
yalnızca hangi kodun çalıştığıdır, ağ tarafı ikisinde de birebir aynıdır.

Bu döküman boyunca:
- **Merkez makine (B)** = ana `docker compose`'un (collector + veritabanları +
  Grafana) çalıştığı bilgisayar.
- **Uzak makine** = KIO modülünün çalıştığı, farklı bilgisayar.

---

## 1. Temel fikir: push tabanlı, tek yönlü

Mimari **push tabanlıdır**. KIO, telemetrisini merkezi collector'a **kendisi
iter**; merkez makine KIO'ya doğru hiçbir bağlantı açmaz, KIO'yu "scrape"
etmez. Yani gereken tek şey:

> Uzak makine → Merkez makine `:4317` (OTLP/gRPC) yönünde **giden** bir TCP
> bağlantısı kurabilmeli.

Bunun pratik sonucu: KIO'nun nerede çalıştığı (aynı LAN, başka şehir, NAT
arkası) fark etmez — collector'a ulaşabildiği sürece çalışır. Bu yüzden
"uzaklaştırma" için **kod değişmez**, sadece bir ortam değişkeni
(`OTEL_EXPORTER_OTLP_ENDPOINT`) değişir.

### Portlar

| Port | Protokol | Ne için | Kim kullanır |
|------|----------|---------|--------------|
| **4317** | OTLP/**gRPC** | Metrikler + loglar + trace'ler | Bu repodaki tüm KIO'lar (varsayılan) |
| 4318 | OTLP/**HTTP** | Aynı üç sinyal, HTTP taşıması | gRPC yerine HTTP tercih eden istemciler |
| 3000 | HTTP | Grafana arayüzü | Dashboard'ları uzaktan açmak isteyen kişi |
| 3001 | HTTP | Langfuse arayüzü + API | (Opsiyonel) Langfuse akışını kullanan KIO'lar |

> **En sık hata:** endpoint'i `:4318`'e ayarlamak. Bu repodaki istemciler
> **gRPC** kullanır → port **4317** olmalı. 4318 HTTP içindir ve gRPC
> istemcisi oraya bağlanınca sessizce "No data" alırsınız.

---

## 2. Merkez makine (B) tarafında yapılması gerekenler

İyi haber: **collector kodu zaten uzak bağlantıya hazır.** Aşağıdaki iki şey
zaten yerinde:

1. Collector her iki OTLP portunu da tüm arayüzlerden dinliyor —
   `otel-collector/config.yaml` içinde `endpoint: 0.0.0.0:4317` /
   `0.0.0.0:4318` (yalnızca `127.0.0.1` değil).
2. `docker-compose.yml`, bu portları host'a yayınlıyor (`"4317:4317"`,
   `"4318:4318"`) — Docker bunları varsayılan olarak `0.0.0.0` üzerinde açar,
   yani LAN'dan erişilebilir.

Geriye tek bir şey kalıyor: **güvenlik duvarında gelen (inbound) portu açmak.**
Bu, "No data" sorununun en yaygın nedenidir.

### 2a. Windows 11 (Docker Desktop) — güvenlik duvarı kuralı

Merkez makine Windows ise, PowerShell'i **Yönetici** olarak açıp:

```powershell
New-NetFirewallRule -DisplayName "AI4SWENG OTLP gRPC" -Direction Inbound -LocalPort 4317 -Protocol TCP -Action Allow
New-NetFirewallRule -DisplayName "AI4SWENG OTLP HTTP" -Direction Inbound -LocalPort 4318 -Protocol TCP -Action Allow
```

Grafana veya Langfuse'a da başka makineden erişilecekse (opsiyonel):

```powershell
New-NetFirewallRule -DisplayName "AI4SWENG Grafana"  -Direction Inbound -LocalPort 3000 -Protocol TCP -Action Allow
New-NetFirewallRule -DisplayName "AI4SWENG Langfuse" -Direction Inbound -LocalPort 3001 -Protocol TCP -Action Allow
```

Kuralı geri almak için: `Remove-NetFirewallRule -DisplayName "AI4SWENG OTLP gRPC"`.

### 2b. Linux (native Docker Engine)

`ufw` kullanıyorsanız:

```bash
sudo ufw allow 4317/tcp
sudo ufw allow 4318/tcp
# opsiyonel: sudo ufw allow 3000/tcp ; sudo ufw allow 3001/tcp
```

`firewalld` kullanıyorsanız:

```bash
sudo firewall-cmd --permanent --add-port=4317/tcp --add-port=4318/tcp
sudo firewall-cmd --reload
```

### 2c. Merkez makinenin adresini öğrenme

Uzak makinenin `OTEL_EXPORTER_OTLP_ENDPOINT`'ine yazacağınız adres budur.

- **Windows:** `ipconfig` → "IPv4 Address" (ör. `192.168.1.50`). Ya da:
  ```powershell
  (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -ne 'WellKnown' }).IPAddress
  ```
- **Linux:** `ip -4 addr show` ya da `hostname -I`.
- **Tailscale kuruluysa:** `tailscale ip -4` (ör. `100.101.102.103` — bu adres
  hiç değişmez, bkz. §3c).

---

## 3. Bağlantı seçenekleri — hangisini seçmeli?

Üç yol var. Aynı ağdaysanız §3a en basit; farklı ağlardaysanız §3c önerilir.

### 3a. Aynı LAN (aynı ev/ofis ağı) — en basit

İki makine aynı yerel ağdaysa hiçbir VPN gerekmez. Uzak makinede:

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://<merkez-LAN-IP>:4317
```

> **Statik IP tavsiyesi:** LAN IP'leri DHCP ile zamanla değişebilir. Merkez
> makine yeniden başlayınca IP değişirse tüm uzak KIO'lar "No data"ya düşer.
> Bunu önlemenin en temiz yolu, router'ınızda merkez makinenin MAC adresine
> **DHCP rezervasyonu** (sabit IP ataması) yapmaktır — böylece adres kalıcı
> olur, uzak KIO'ların `.env`'ine dokunmanız gerekmez. Alternatif: merkez
> makineye işletim sisteminden statik IP vermek.

### 3b. Statik / genel (public) IP + port yönlendirme — dikkatli olun

Makineler farklı ağlardaysa ve elinizde gerçek bir statik/genel IP varsa,
router'da `4317` portunu merkez makineye **yönlendirebilirsiniz** (port
forwarding). Ancak bu, portu **genel internete açar**.

> ⚠️ Bu paket varsayılan olarak **kimlik doğrulamasız (insecure)** dinler.
> 4317'yi doğrudan internete açmak, isteyen herkesin sahte metrik
> gönderebilmesi demektir. Genel internete açacaksanız **önce** §4'teki
> TLS + Bearer token adımını uygulayın. Genelde §3c (VPN) daha güvenli ve daha
> az uğraştırıcıdır — hiçbir portu internete açmadan çalışır.

### 3c. Tailscale / ZeroTier (VPN) — farklı ağlar için önerilen ✅

Tailscale (veya ZeroTier), her iki makineyi **aynı sanal ağda**ymış gibi
gösterir. Her cihaza NAT/router arkasında olsa bile kalıcı, değişmeyen bir
`100.x.y.z` adresi verir; port yönlendirmeye, statik/genel IP satın almaya
gerek kalmaz ve trafik uçtan uca şifrelidir.

**"Aynı ağ" derdine gerek yok — statik IP de gerekmez.** Tailscale'in tüm amacı
budur.

Kurulum:
1. Her iki makineye Tailscale kurun ve aynı hesap/tailnet'e giriş yapın.
2. Merkez makinede adresi öğrenin: `tailscale ip -4` → ör. `100.101.102.103`.
3. Uzak makinede:
   ```
   OTEL_EXPORTER_OTLP_ENDPOINT=http://100.101.102.103:4317
   ```
   (Tailscale IP'si değişmediği için bunu bir daha güncellemeniz gerekmez.
   Tailscale içi trafik zaten şifreli olduğundan LAN'daki gibi `http://` +
   insecure yeterlidir; §4'teki ek TLS şart değildir.)

Bir KIO sahibini eklemenin iki yolu:
- **Tam tailnet üyeliği** ("Invite") — ekip arkadaşınızsa mantıklı; ACL'lerle
  hangi cihazları görebileceğini sınırlayabilirsiniz.
- **Tek cihaz paylaşımı** ("Share" — admin panelinde merkez makinenizin
  yanında) — dış bir kişiye tüm tailnet'i açmadan yalnızca o tek makineyi
  paylaşır. Tek seferlik/dış bir alıcı için en az yetkiyi veren yol, muhtemelen
  aradığınız budur.

> Tailscale'de firewall açmanıza bile gerek olmayabilir: trafik `tailscale0`
> arayüzünden gelir. Yine de bağlantı kurulamıyorsa merkez makinede §2'deki
> kuralın Tailscale arayüzünü de kapsadığından emin olun.

---

## 4. Güvenlik / TLS (üretim veya internete açık kurulum)

Varsayılan kurulum, güvenilir bir ağ (aynı LAN veya VPN) içindeki testler için
**kimlik doğrulamasız** çalışır — tıpkı yerel kurulum gibi. Trafiği güvenilir
olmayan bir ağdan geçireceksiniz (§3b) veya üretime alacaksanız:

1. **Merkez collector'a** bir `bearertokenauth` extension'ı ekleyin ve OTLP
   receiver'ında TLS'i etkinleştirin (`otel-collector/config.yaml`).
2. **Uzak KIO'da** endpoint'i `https://...:4317` yapın ve token'ı ekleyin:
   ```
   OTEL_EXPORTER_OTLP_ENDPOINT=https://<merkez-adres>:4317
   OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <TOKEN>
   ```
   OpenTelemetry SDK bu iki ortam değişkenini otomatik okur — **KIO kodunda
   ekstra değişiklik gerekmez** (`https://` şeması TLS'i açar, header yetkilendirir).

Bu, Integration Contract §1.2'nin resmi/normatif yoludur; yerel/LAN kurulumu
onun `insecure` gevşetilmiş halidir.

---

## 5. Doğrulama — bağlanabiliyor muyum?

Sırasıyla:

**a) Port açık mı? (hızlı TCP testi)**

- Uzak makine Windows ise:
  ```powershell
  Test-NetConnection <merkez-adres> -Port 4317
  ```
  `TcpTestSucceeded : True` görmelisiniz.
- Uzak makine Linux ise:
  ```bash
  nc -zv <merkez-adres> 4317
  ```

**b) Uçtan uca akıyor mu? (gerçek export testi)**

`reference-client/check_connectivity.py` hem TCP'yi hem de gerçek bir OTLP
export'unu (tek bir `kio.heartbeat`) dener ve nerede takıldığını net söyler:

```bash
cd reference-client
pip install -r requirements.txt
OTEL_EXPORTER_OTLP_ENDPOINT=http://<merkez-adres>:4317 python check_connectivity.py
```

Başarılıysa Grafana → **KIO Detail** → `KIO` açılır menüsünde `kio-preflight`
(ya da verdiğiniz `KIO_ID`) ~30-60 saniye içinde görünür.

---

## 6. Sorun giderme (hızlı tablo)

| Belirti | Olası neden | Çözüm |
|---|---|---|
| `check_connectivity.py` **[1/2] FAIL** | Yanlış IP / firewall kapalı / farklı ağ | §2 (firewall), §2c (doğru adres), §3 (aynı ağ/VPN) |
| **[1/2] OK ama [2/2] FAIL** | Port açık ama collector export'u reddediyor (ör. TLS/auth gerekli) | §4 (TLS + token) veya endpoint şemasını (`http` vs `https`) kontrol et |
| TCP başarılı, Grafana'da yine "No data" | Endpoint `:4318`'e (HTTP) ayarlı, istemci gRPC | Portu **4317** yap |
| Bir süre çalıştı, sonra kesildi | Merkez makinenin LAN IP'si DHCP ile değişti | §3a statik IP / DHCP rezervasyonu |
| KIO görünüyor ama seri karışık | Aynı `kio.id` iki kaynaktan gönderiyor | Benzersiz `kio.id` kullan (bkz. INTEGRATION.md / README) |
| KIO "stale" işaretlendi | Heartbeat >120s kesildi (KIO durdu ya da ağ koptu) | KIO'nun ayakta ve export ediyor olduğunu doğrula |

Ayrıntılı entegrasyon adımları için → [`INTEGRATION.md`](INTEGRATION.md).
