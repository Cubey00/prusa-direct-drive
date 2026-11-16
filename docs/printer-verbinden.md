# Printer verbinden met een laptop (ook op de TU)

Dit stappenplan is voor iedereen in het team die de Core One met een **andere laptop** wil
gebruiken: printen via PrusaLink, de printer besturen en de krachtdata ontvangen.

## Waarom een directe kabel?

Op de TU werkt de **netwerkaansluiting in de muur niet** voor deze opstelling. Die zit op een
ander, afgeschermd netwerk dan eduroam, en het TU-netwerk laat apparaten elkaar niet bereiken.
Daardoor werken PrusaLink en de **krachtdata** (UDP, poort 8514) niet. We verbinden de printer
daarom met een **netwerkkabel rechtstreeks aan de laptop**, met vaste adressen:

| Apparaat | Adres |
|---|---|
| Printer | `192.168.50.2` |
| Laptop (Ethernet-poort) | `192.168.50.1` |
| Subnetmasker | `255.255.255.0` |

Je laptop houdt **Wi-Fi (eduroam) voor internet**; de kabel is alleen voor de printer.

**Iedere laptop krijgt hetzelfde adres `192.168.50.1`.** De printer onthoudt naar welk adres
hij de krachtdata stuurt. Doordat elke laptop hetzelfde adres gebruikt, hoef je op de printer
niets te veranderen als iemand anders aansluit. Er kan wel maar één laptop tegelijk aan de
kabel.

## Wat je nodig hebt

- **Een netwerkkabel** van de printer naar de laptop. Een gewone kabel werkt; je hebt geen
  crossover-kabel nodig.
- **Een netwerkpoort op de laptop**, of een **USB-C- of USB-naar-Ethernet-adapter**.
- **De USB-kabel** van de printer naar de laptop, om de printer te besturen.
- **Beheerdersrechten** op de laptop, om één keer een vast adres in te stellen.
- **De repository** (branch `development-bep-groep` of `feat/kalibratie`) met Python en
  `pip install -r requirements.txt`.
- **Het wachtwoord van PrusaLink**. Dat staat op de printer onder
  **Settings > Network > PrusaLink**; de gebruiker is `maker`.

---

## Stap 1: printer (is al gedaan; alleen nodig na een reset)

De printer staat al vast op `192.168.50.2`. Controleer dat onder
**Settings > Network > Ethernet (LAN)**. Alleen als daar iets anders staat (na een reset,
een firmware-update of een andere printer), doe je dit:

1. Zet [`printer_network/prusa_printer_settings.ini`](../printer_network/prusa_printer_settings.ini)
   in de **hoofdmap** van een FAT32-USB-stick. Laat de naam precies zo.
2. Steek de stick in de printer en kies **Settings > System > Load Settings from File**. Let
   op: niet het menu onder *Network* of *Wi-Fi*, want dat laadt alleen Wi-Fi-gegevens.
3. **Herstart de printer.** Staat er een `.bbf` (firmware) op de stick en vraagt de printer of
   hij die moet installeren, kies dan **Nee**, tenzij je dat bewust wilt.
4. Controleer dat er nu `192.168.50.2` staat onder **Ethernet (LAN)**.

Het bestand ziet er zo uit:

```ini
[eth::ipv4]
type=static
addr=192.168.50.2
mask=255.255.255.0
gw=192.168.50.1

[network]
dns4=192.168.50.1
```

## Stap 2: kabel aansluiten

Steek de netwerkkabel van de printer in je laptop. **Niet in de muur.**

## Stap 3: vast adres op je laptop (één keer per laptop)

### Windows

Gebruik het script [`printer_network/laptop_setup.ps1`](../printer_network/laptop_setup.ps1).
Het doet twee dingen:
- het geeft de Ethernet-poort het vaste adres `192.168.50.1`, **zonder gateway**, zodat je
  internet via Wi-Fi blijft gaan;
- het voegt een **firewallregel** toe die de krachtdata van de printer binnenlaat (alleen
  binnenkomend UDP 8514).

1. Open een gewoon **PowerShell**-venster in de projectmap.
2. Plak dit en druk op Enter. Pas het pad aan naar waar de repository op jouw laptop staat:
   ```powershell
   Start-Process powershell -Verb RunAs -ArgumentList '-NoExit -ExecutionPolicy Bypass -File "C:\pad\naar\prusa-direct-drive\printer_network\laptop_setup.ps1"'
   ```
3. Klik op **Ja** bij de vraag van Windows. Het nieuwe venster eindigt met **"Done"** in groen.

**Heet de netwerkpoort niet `Ethernet`**, bijvoorbeeld *Ethernet 2* bij een USB-adapter? Zoek de
naam op met `Get-NetAdapter` en pas `$Adapter` bovenaan het script aan.

**Terugdraaien:** voer [`printer_network/laptop_undo.ps1`](../printer_network/laptop_undo.ps1)
op dezelfde manier uit. Dan staat Ethernet weer op automatisch en is de firewallregel weg.

### macOS

1. **Systeeminstellingen > Netwerk**. Kies de Ethernet-verbinding (of de USB-adapter) en klik
   op **Details... > TCP/IP**.
2. Zet **IPv4 configureren** op **Handmatig** en vul in:
   - IP-adres: `192.168.50.1`
   - Subnetmasker: `255.255.255.0`
   - Router: **leeg laten**
3. Klik op **OK**. Staat de **firewall** aan (*Systeeminstellingen > Netwerk > Firewall*), sta
   dan binnenkomende verbindingen voor **Python** toe wanneer macOS daarom vraagt. Anders komt
   de krachtdata niet binnen.

**Terugdraaien:** zet **IPv4 configureren** weer op **Via DHCP**.

## Stap 4: controleren

1. Ping de printer:
   ```bash
   ping 192.168.50.2
   ```
   Je moet antwoord krijgen. Op macOS stop je met Ctrl+C.
2. Open in je browser **`http://192.168.50.2`**. Daar hoort de inlogpagina van PrusaLink te
   staan.

## Stap 5: de software

1. Start de path programmer in de projectmap:
   ```bash
   python -m pathprogrammer
   ```
2. **PrusaLink (om te printen):** het adres (`192.168.50.2`) en de gebruiker (`maker`) staan al
   ingevuld; ze komen uit [`machine_settings.toml`](../machine_settings.toml) onder `[prusalink]`.
   Je hoeft alleen **één keer het wachtwoord** in te vullen:
   - Klik in het blok **Printer (PrusaLink)** op **Printer settings...**
   - Vul bij **Password** het wachtwoord van PrusaLink in. Laat *API key* leeg.
   - Klik op **Test connection**; je hoort *"Connected: printer is IDLE"* te zien. Klik daarna
     op **Save**.

   Het wachtwoord wordt alleen op jouw laptop bewaard, niet in de repository. Heeft de printer
   een ander adres, verander het dan in `machine_settings.toml`, of alleen voor jouw laptop in
   *Printer settings...*.
3. **USB en krachtdata (om te kalibreren en te meten):**
   - Sluit de USB-kabel aan en klik op **Open visual calibrator**.
   - De eerste keer, of als de printer een ander adres had, vraagt de printer **op zijn
     scherm** of de metrics naar `192.168.50.1` mogen. **Bevestig dat.**
   - Werkt het, dan beweegt de krachtgrafiek en loopt **"Current: X … Y … Z …"** mee als je
     jogt.
   - Maar één programma kan de USB-poort tegelijk gebruiken. **Sluit de calibrator** voordat
     je *Execute program* start.

---

## Problemen oplossen

| Wat je ziet | Oorzaak en oplossing |
|---|---|
| Ping: geen antwoord | Zit de kabel in de laptop, en niet in de muur? Staat het adres van de laptop goed (`ipconfig` of `ifconfig`)? Staat er `192.168.50.2` op de printer? |
| *Start this from a Terminal opened as administrator* | Het venster draait niet als beheerder. Gebruik het commando met `-Verb RunAs` uit stap 3 |
| *Cannot reach the printer* in de app | Zie de regel over ping. Gebruik als adres in de app `192.168.50.2` |
| *PrusaLink refused the login* | Gebruiker moet `maker` zijn; controleer het wachtwoord op de printer |
| Geen krachtdata (de grafiek blijft leeg) | De firewall blokkeert UDP 8514 (Windows: draai `laptop_setup.ps1`; macOS: sta Python toe), of de vraag op het scherm van de printer is niet bevestigd |
| De software kiest een verkeerd adres voor de krachtdata | Zet het vast. Windows (PowerShell): `$env:PRINTER_METRICS_IP="192.168.50.1"; python -m pathprogrammer`. macOS: `PRINTER_METRICS_IP=192.168.50.1 python -m pathprogrammer` |
| *could not open port* / *Toegang geweigerd* (USB) | Een ander programma gebruikt de USB-poort al: de calibrator, PrusaSlicer of een tweede instantie. Sluit die |
| Op de printer staat `0.0.0.0` na het laden van het bestand | Het bestand is niet goed gelezen. Controleer de naam, of het in de hoofdmap staat en of je het menu **System** gebruikte, en herstart de printer. Lukt het nog steeds niet: zet de printer op DHCP en laat Windows het adres uitdelen met *Internetverbinding delen* (dan wordt het laptopadres `192.168.137.1`) |

## Terug naar het normale netwerk

- **Laptop:** voer `laptop_undo.ps1` uit (Windows), of zet IPv4 weer op DHCP (macOS).
- **Printer:** zet **Ethernet** onder *Settings > Network* weer op **DHCP**, of laad een
  `prusa_printer_settings.ini` met `type=dhcp` onder `[eth::ipv4]`.
