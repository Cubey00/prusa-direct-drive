# How to start

Stap voor stap: wat je de **eerste keer** op een laptop moet doen om de Touch Path Programmer te
gebruiken. Daarna start je hem gewoon met de snelkoppeling op je bureaublad.

Deze handleiding gaat uit van **Windows**. Voor een Mac, zie [docs/macos-setup.md](docs/macos-setup.md).

| Stap | Wat | Hoe vaak |
|---|---|---|
| 1 | Python installeren | één keer per laptop |
| 2 | Git en de code ophalen | één keer per laptop |
| 3 | Python-pakketten installeren | één keer, en na een nieuwe `requirements.txt` |
| 4 | PrusaSlicer installeren | één keer per laptop |
| 5 | IDS peak SDK (camera-driver) installeren | één keer per laptop |
| 6 | Netwerkkabel en vast adres | één keer per laptop |
| 7 | Snelkoppeling maken | één keer per laptop |
| 8 | Eerste keer starten: PrusaLink-wachtwoord | één keer per laptop |
| 9 | Controleren dat alles werkt | elke keer even kijken |

---

## Stap 1: Python installeren

1. Download **Python 3.12** (64-bit) van [python.org/downloads](https://www.python.org/downloads/).
2. Vink in het eerste scherm van de installer **"Add python.exe to PATH"** aan.
3. Klik op **Install Now**.
4. Controleer het: open een opdrachtprompt (Windows-toets, typ `cmd`) en typ:

   ```bash
   python --version
   ```

   Je moet `Python 3.12.x` zien.

## Stap 2: Git en de code ophalen

1. Installeer **Git** van [git-scm.com](https://git-scm.com/download/win). De standaardinstellingen
   zijn goed.
2. Haal de code op en ga naar de teambranch:

   ```bash
   git clone https://github.com/Cubey00/prusa-direct-drive.git
   ```

   ```bash
   cd prusa-direct-drive
   ```

   ```bash
   git checkout development-bep-groep
   ```

3. Later de nieuwste versie ophalen doe je in die map met:

   ```bash
   git pull
   ```

> **Let op:** zet de firmware-repository (`prusa-direct-drive-firmware`) **niet** in deze map of
> in de map erboven. Die hoort apart.

## Stap 3: Python-pakketten installeren

In de map `prusa-direct-drive`:

```bash
python -m pip install -r requirements.txt
```

Dit installeert alles wat het programma gebruikt: PySide6 (de vensters), numpy, trimesh,
pyserial (USB naar de printer), requests (PrusaLink), tifffile (foto's) en de Python-pakketten
van de camera, `ids_peak` en `ids_peak_ipl`.

Krijg je later een melding als *"No module named …"*? Draai dit commando dan opnieuw: er is
waarschijnlijk een pakket bijgekomen.

## Stap 4: PrusaSlicer installeren

Het programma gebruikt PrusaSlicer om de printplaten te slicen en om G-code in 3D te laten zien.

1. Download **PrusaSlicer 2.9 of nieuwer** van
   [prusa3d.com/page/prusaslicer_424](https://www.prusa3d.com/page/prusaslicer_424/). Oudere
   versies kennen de Core One niet.
2. Installeer het op de standaardplek (`C:\Program Files\Prusa3D\PrusaSlicer`). Daar zoekt het
   programma het vanzelf.

Staat PrusaSlicer ergens anders? Zet dan het pad in `slicing/config.py` bij `SLICER_EXE`.

## Stap 5: IDS peak SDK (camera-driver) installeren

De Python-pakketten uit stap 3 praten met de camera via de **IDS peak SDK**. Die bevat de
USB3-driver en moet je los installeren. Zonder SDK zegt het cameravenster:
*"IDS peak SDK (camera-driver) is niet geïnstalleerd op deze computer"*.

1. Ga naar [ids-imaging.com](https://www.ids-imaging.com) → **Downloads**, kies jullie camera en
   download **IDS peak** voor Windows (64-bit). Kies bij voorkeur dezelfde reeks als het
   Python-pakket (`ids_peak` 1.17).
2. Start de installer **als beheerder** en laat de **USB3 Vision-driver** aangevinkt.
3. **Herstart de laptop.** De installer zet de omgevingsvariabele `GENICAM_GENTL64_PATH`; die
   wordt pas na een herstart overal gezien.
4. Sluit de camera aan op een **USB 3-poort** (blauw, of met "SS").
5. Controleer het eventueel met **IDS peak Cockpit**, dat met de SDK is meegeïnstalleerd: daar
   moet de camera in de lijst staan.

Heb je geen camera nodig, dan kun je deze stap overslaan. De rest van het programma werkt ook
zonder.

## Stap 6: netwerkkabel en vast adres (voor PrusaLink en de krachtdata)

Printen via het programma (PrusaLink) en de krachtdata van de load cell gaan over een
**netwerkkabel rechtstreeks van de printer naar de laptop**. De netwerkaansluiting in de muur
van de TU werkt hier niet voor.

Het volledige stappenplan staat in [docs/printer-verbinden.md](docs/printer-verbinden.md). Kort:

1. Sluit een netwerkkabel aan van de printer naar je laptop (eventueel via een
   USB-naar-Ethernet-adapter).
2. Geef je laptop één keer het vaste adres `192.168.50.1`. Start daarvoor
   [printer_network/laptop_setup.ps1](printer_network/laptop_setup.ps1) **als beheerder**. Het
   script zet ook de firewallregel voor de krachtdata (UDP 8514). Ongedaan maken doe je met
   `laptop_undo.ps1`.
3. De printer staat al op `192.168.50.2`. Is dat na een reset niet meer zo, volg dan stap 1 van
   [docs/printer-verbinden.md](docs/printer-verbinden.md).
4. Sluit ook de **USB-kabel** van de printer aan op de laptop. Daarover bestuurt het programma de
   printer (bewegen, kalibreren, aantikken). Het programma vindt de printer zelf. Vindt hij hem
   niet, dan staat er *"No Prusa printer found"*: controleer of de kabel goed vastzit en de printer
   aanstaat.

Je laptop houdt gewoon Wi-Fi (eduroam) voor internet.

> **Firmware:** het programma werkt met de **teamfirmware** (Core One, 6.10.100, met onder andere
> de aangepaste M410-stop en de grenzen van de afstandsblokjes). Op een printer met standaard
> Prusa-firmware werken bewegen, kalibreren en aantikken niet goed.

## Stap 7: snelkoppeling maken

Maak in de map `prusa-direct-drive` één keer de snelkoppeling aan:

```bash
python tools/make_shortcut.py
```

Er staat nu **"Touch Path Programmer"** (met het blauwe logo) op je bureaublad en in de
projectmap. Dubbelklikken start het programma, zonder zwart opdrachtvenster.

Starten zonder snelkoppeling kan ook, vanuit de projectmap:

```bash
python -m pathprogrammer
```

## Stap 8: eerste keer starten en PrusaLink instellen

1. Start het programma met de snelkoppeling.
2. Klik in het vak **Printer (PrusaLink)** op **Printer settings...**
3. Vul het **wachtwoord** in. Dat staat op de printer onder
   **Settings > Network > PrusaLink**; de gebruiker is `maker` en het adres `192.168.50.2` staat
   er al. Heeft de printer een **API key** in plaats van een wachtwoord, vul dan die in.
4. Klik op **Test connection**. Je moet *"Connected: printer is …"* zien. Klik dan op **Save**.

Het wachtwoord wordt alleen op deze laptop bewaard, niet in de repository. Vanaf nu maakt het
programma zelf verbinding zodra de printer is aangesloten. Linksboven in het printervak staat
dan **● Verbonden**.

## Stap 9: controleren dat alles werkt

| Waar | Wat je moet zien | Zo niet |
|---|---|---|
| Printer (PrusaLink) | **● Verbonden** en de printerstatus | Kabel, vast adres (stap 6), wachtwoord (stap 8) |
| Foto control | Live camerabeeld, **Maak foto** is blauw | SDK (stap 5), camera op USB 3, laptop herstart |
| Print plates | Na **Mini test set**: "All plates sliced" | PrusaSlicer 2.9+ (stap 4) |
| Kalibratie | Bij **Start kalibratie**: de stappen in het kalibratievenster, geen foutmelding over de printer | USB-kabel naar de printer, printer aan |

---

## Daarna: zo gebruik je het programma

1. **Platen maken.** Kies in **Print plates** het filament dat in de printer zit, en klik op
   **Dataset...** (jullie eigen onderdelen) of **Mini test set** (twee kleine testonderdelen).
   Het programma verdeelt de onderdelen over platen en slicet ze. De geselecteerde plaat wordt
   meteen als model geladen.
2. **Printen.** Haal de sensor van de printkop. Klik daarna op de groene knop **Print this plate**
   en blijf bij de printer tot de eerste laag ligt.
3. **Kalibreren.** Zet de sensor terug op de kop en klik op de blauwe knop **Start kalibratie**.
   Volg de stappen: per kegel F1, F2 en F3 eerst **Ga naar kegel**, dan met de jogknoppen de
   sensor precies op de top zetten, en dan **Positie insturen**. Boven de knop zie je de
   afstand tussen nozzle en sensorkop. Pas die alleen aan als je zeker weet dat hij niet klopt;
   de kalibratie meet hem zelf.
4. **Foto's.** In **Foto control** zie je altijd het live camerabeeld. **Maak foto** slaat een
   TIFF op in de map `photos/`. Met **Map...** kies je een andere map. Onder het beeld stel je de
   **sluitertijd** (hoe lang de camera per beeld licht opvangt) en de **gain** in. De LED-verlichting
   (5 V) staat los van het programma: die zet je zelf aan.
5. **Aantikken.** Open **Points in ascending order** en zet punten op het model. Start daarna met
   **Execute program**. De rode **STOP** werkt altijd.

## Veelvoorkomende problemen

- **"No Prusa printer found"**: de USB-kabel zit niet goed, of de printer staat uit. Bij twee
  aangesloten Prusa's kies je de poort met `PORT_OVERRIDE` in `gantry_controls.py`.
- **Printer niet bereikbaar (PrusaLink)**: controleer de netwerkkabel en het vaste adres. In een
  opdrachtprompt moet `ping 192.168.50.2` antwoorden.
- **Geen krachtdata**: de firewallregel voor UDP 8514 ontbreekt; draai `laptop_setup.ps1`
  opnieuw als beheerder. Vraagt de printer op zijn scherm om een metrics-adres te bevestigen,
  bevestig dat dan.
- **"Bed omhoog" wordt geweigerd tijdens het kalibreren**: de sensor voelt meer dan 100 g, dus hij
  drukt al op de kegel. Laat het bed iets zakken. Gebeurt het terwijl de sensor nergens tegenaan
  zit, dan is de load cell verlopen (drift).
- **Het programma start niet na een `git pull`**: draai
  `python -m pip install -r requirements.txt` opnieuw.
- **Instellingen van de opstelling** (werkgebied, kegels, krachtgrenzen, sensorafstand) staan
  allemaal in `machine_settings.toml`. Pas ze daar aan, nergens anders.
