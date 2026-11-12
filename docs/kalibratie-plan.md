# Kalibratieplan: van geprinte plaat naar gekalibreerde sensor

**Doel.** Na het printen en het wisselen van printkop naar sensor klik je op **Kalibreer**. De sensor zoekt dan zelf de drie geprinte kegels F1, F2 en F3 op en bepaalt waar de plaat ligt (X/Y), hoe hoog en hoe scheef het bed staat (Z). Hij controleert het resultaat en geeft daarna pas het meten vrij.

Dit plan bouwt voort op het ontwerpverslag (*Plan van aanpak, Tactile Sensing Robot*). Uit de morfologische kaart is gekozen voor "kalibratiepunten printen en die meten vóór de echte metingen". Het verslag eist ook dat de sensor na af- en weer opzetten op dezelfde plek zit, dat er zo min mogelijk speling is en dat zo veel mogelijk van het bed bruikbaar blijft.

Alle getallen die van de opstelling afhangen staan in **[`machine_settings.toml`](../machine_settings.toml)**: de afstand van nozzle tot sensor, het bereik met de stops, de cameraschaal, de kegels en de krachtgrenzen. De code leest ze daar. Zie het [laatste hoofdstuk](#het-instellingenbestand).

---

## Wat er al is

| Onderdeel | Waar |
|---|---|
| Plaat met F1 (linksvoor), F2 (rechtsvoor) en F3 (linksachter); het manifest kent hun bedcoördinaten en tophoogte (5 mm) | `slicing/`, `plates/<set>/plate_NN.json` |
| Plaat openen in de path programmer; de facepunten van F1–F3 worden uit het manifest ingevuld | *Open plate in programmer* |
| Machinepositie per punt doorsturen vanuit de calibrator (werkelijke positie uit de metrics) | `GUI.py`, knoppen F1/F2/F3 |
| XY-fit over 3 punten (kleinste kwadraten), restfout per punt, controle op punten op één lijn | `pathprogrammer/calibration.py` |
| Aanraken met de loadcell: snel benaderen, terug, langzaam opnieuw zoeken, contact-Z | `touch.py` |
| Printstatus via PrusaLink: we weten wanneer de print klaar is | `prusalink.py`, Printer-blok |

Wat ontbreekt: de Z-kalibratie (bedvlak), het automatisch vinden van de punten, het camerabeeld, de afstand tussen nozzle en sensor, acceptatiecriteria die het meten **blokkeren**, het opslaan van de kalibratie en een controle op drift.

---

## Overzicht: drie lagen

| Laag | Hoe vaak | Wat |
|---|---|---|
| **A. Opstelling** | Eenmalig, en na een andere mount, een ander polymeer of andere stops | Afstand nozzle–sensor, cameraschaal, loadcell-schaal, bereik |
| **B. Plaat** | Na elke wissel, voor elke plaat: de knop **Kalibreer** | F1–F3 vinden, XY-transformatie, Z-vlak, controle, opslaan |
| **C. Drift** | Tijdens het meten | Af en toe één punt opnieuw aanraken; bij afwijking pauzeren |

---

## A. Kalibratie van de opstelling (zelden)

De uitkomsten komen in `machine_settings.toml`. Zet daar `measured = true`, dan stopt de software met waarschuwen.

### A1. Afstand nozzle → sensor (`[sensor] offset_x/y/z`)

Deze afstand is **geen voorwaarde** voor een goede plaatkalibratie: de F1–F3-fit neemt elke verschuiving vanzelf mee. Hij is wel nodig om:
- te **voorspellen** waar de kegels zitten, zodat de sensor er zelf naartoe kan rijden;
- het **bereik** van de sensor te bepalen. Dat is niet hetzelfde als het bereik van de nozzle;
- de juiste **hoogte** te weten. De sensor zit lager of hoger dan de nozzle.

De meting komt gratis uit stap B. De nozzle heeft de kegels geprint op hun bedcoördinaten (uit het manifest). Vindt de sensor F1 op machinepositie (X, Y), dan is de afstand `(X − F1.x, Y − F1.y)`. De afstand in Z is `contact-Z op de top − 5 mm`. De software logt deze afstand bij elke kalibratie. Na een paar platen zet je het gemiddelde in het instellingenbestand. De eerste keer jog je met de hand naar F1, zie B3a.

### A2. Cameraschaal (`[camera] mm_per_px`, `centre_px`)

- **Schaal**: druk de sensor licht op een kegeltop, schuif 1,00 mm in X en daarna 1,00 mm in Y en druk opnieuw. De verschuiving van het fringe-centrum in pixels geeft de mm per pixel en of het beeld gedraaid staat.
- **Centrum**: de pixel waar het contactpunt zit als de sensor recht boven de top staat. Die vind je met B3c (tastend zoeken) of door symmetrisch te benaderen.
- **Opnieuw doen** na het wisselen van het polymeer of het verstellen van de lens. De draaidop met de PMMA-rand of de conische centrering (zie het verslag, VIII-A.2) zou dit herhaalbaar moeten maken. B5 controleert dat.

### A3. Loadcell-schaal

Bestaat al: *Weight calibration…* in `GUI.py` schrijft `calibration.json`.

### A4. Bereik met de stops (`[work_area] machine_x/y`)

Jog met de sensor erop naar de vier uitersten en noteer M114. De software weigert dan meetpunten buiten dit bereik.

---

## B. Kalibratie per plaat: de knop **Kalibreer**

### Werkwijze voor de gebruiker

```
Print klaar (PrusaLink: FINISHED)
  -> "Wissel naar de sensor": instructies; wacht tot het bed is afgekoeld
  -> gebruiker bevestigt de wissel
  -> [Kalibreer]   voortgang per punt: F1, F2, F3 (+ controlepunt)
  -> resultaat: restfouten, kanteling, afstand nozzle-sensor, groen/oranje/rood
  -> [Accepteren] of [Opnieuw]
  -> meten wordt vrijgegeven
```

### B0. Controles vooraf (kalibreren blokkeert bij rood)

- De printer is klaar en niet meer bezig: PrusaLink geeft `FINISHED`, `IDLE` of `STOPPED`.
- Er komt verse loadcell-data binnen. Kijk naar de leeftijd van het laatste sample, niet alleen naar het gemiddelde (zie het takenplan, §1 "watchdog").
- De camera geeft beeld. Dat is pas nodig vanaf B3b.
- Er is een manifest geladen en de plaat is geopend.
- Het bed is afgekoeld onder een grens, bijvoorbeeld 35 °C, zodat het polymeer niet opwarmt.
- Er komt een waarschuwing als er nog `measured = false` in `machine_settings.toml` staat.
- **Nooit `G28` met de sensor erop.** De Z-homing gebruikt de loadcell van de nozzle. Hangt de sensor lager, dan raakt die het bed eerst. De machinecoördinaten van na de print blijven dus de basis.

### B1. Veilige hoogte

Laat het bed eerst zakken (hogere Z) tot boven de hoogste kegel plus het uitsteken van de sensor, en rijd dan pas in XY.

### B2. Naar elke kegel en de hoogte meten

- **Voorspelde positie** = bedcoördinaat uit het manifest + afstand nozzle–sensor (A1). Na de eerste plaat gebruik je de vorige kalibratie, die is nauwkeuriger.
- **Raak de top licht aan** met `touch()` en een lage doelkracht, bijvoorbeeld 10–20 g. Gebruik de langzame zoeksnelheid. Zo beschadigt het polymeer niet en vervormt de top niet.
- **Bewaar de contact-Z** per punt. Die is nodig voor het Z-vlak.

### B3. XY van de top bepalen

Er zijn drie methoden. Bouw ze in deze volgorde; de latere maken de eerdere niet overbodig.

| | Methode | Nauwkeurigheid | Nodig |
|---|---|---|---|
| a | **Handmatig**: jog met een live camerabeeld en een kruisje, dan *Send position* | Hangt van de gebruiker af, ±0,1 mm | Camerabeeld in de calibrator (staat nu uit in `GUI.py`) |
| b | **Camera, automatisch**: druk licht op de top. Het fringe-patroon is concentrisch rond het contactpunt. Bereken het zwaartepunt in pixels, reken om naar mm (A2), stuur bij en herhaal tot de afwijking kleiner is dan 0,05 mm (hoogstens 3 keer) | ±0,05 mm verwacht | A2 en beeldverwerking |
| c | **Tastend zoeken, zonder camera**: meet de contact-Z op een raster van 3×3 punten rond de voorspelde plek (stap 0,5 mm). De top is waar de contact-Z het hoogst is; fit een paraboloïde | ±0,1 mm verwacht, wel ongeveer 9 keer aanraken per punt | Alleen de loadcell |

Methode c is een goede **reserve** en een onafhankelijke **controle** van b.

### B4. Fit

- **XY**: de bestaande gelijkvormigheidstransformatie (verschuiving, rotatie, schaal), van bed of face naar machine.
- **Z**: een vlak door de drie contacthoogtes van de toppen. Een top ligt 5 mm boven het bed. Dat geeft de bedhoogte op elke (x, y), en dus de **verwachte contacthoogte van elk onderdeel** = vlak + onderdeelhoogte uit het manifest. Die gaat als `expect_z` naar `touch()`, zodat het snelle benaderen dicht bij het onderdeel kan stoppen.

### B5. Acceptatie

De criteria zijn voorstellen; stel ze samen vast. Ze komen in `[calibration]` in het instellingenbestand.

| Controle | Groen | Rood (blokkeert het meten) | Waarom |
|---|---|---|---|
| Restfout per punt | < 0,2 mm | > 0,5 mm | Verkeerd punt of slechte meting |
| Schaal \|s − 1\| | < 0,3 % | > 2 % | De printer en de plaat moeten op schaal 1 liggen |
| Rotatie | < 1° | > 3° | De plaat kan niet echt gedraaid liggen: punten verwisseld? |
| Afstanden F1–F2 (230 mm) en F1–F3 (80 mm) | < 0,2 mm | > 0,5 mm | Rechtstreekse controle met de geometrie |
| Kanteling van het bed over de plaat | < 0,3 mm | > 1 mm | Plaat niet vlak of sensor scheef |
| Verandering afstand nozzle–sensor t.o.v. vorige keer | < 0,3 mm | > 1 mm | Sensor anders gemonteerd? (herhaalbaarheid, takenplan §9) |

Nu geeft een te grote restfout alleen een **waarschuwing**. Het takenplan vraagt om **weigeren** (§3), dus rood moet het meten blokkeren.

### B6. Controle op een punt dat niet in de fit zit

Het takenplan vraagt om "posities meten die niet gebruikt zijn bij de kalibratie" (§3). Voorstel: een **vierde kegel F4 rechtsachter**. Die is alleen voor controle en gaat niet in de fit. De software voorspelt waar F4 zit, meet hem en logt de fout. Dat is het eerlijkste getal voor de nauwkeurigheid van de kalibratie. F4 kost bijna niets: een extra kegel in `slicing/` en een extra veld in het manifest.

### B7. Opslaan

Sla `plates/<set>/plate_NN_calibration.json` op naast de plaat, met:
- tijd, mount, polymeer of sensorconfiguratie, gebruikte methode (a/b/c);
- per punt: voorspelde en gemeten XY en Z, aantal pogingen, restfout;
- de transformatie (a, b), het Z-vlak, de afstand nozzle–sensor en de fout op het controlepunt;
- een kopie van de gebruikte `machine_settings.toml`-waarden.

Elke meting in het run-log verwijst naar dit bestand. Dan kun je een dataset later altijd herleiden tot de kalibratie waarmee hij gemaakt is.

---

## C. Drift tijdens het meten

Het verslag noemt als opties: kalibreren bij een afwijking, per tijdseenheid of na X metingen. Combineer ze:
- Raak na elke **N metingen** of **T minuten** F1 opnieuw aan, alleen contact-Z en het camerazwaartepunt. Dat kost een paar seconden.
- Afwijking groter dan de drempel (bijvoorbeeld 0,1 mm in Z of 0,2 mm in XY): **pauzeer** en stel voor om opnieuw te kalibreren.
- Leg N, T en de drempels vast in `[calibration]`.

---

## Nauwkeurigheid (schatting, nog te meten)

| Bron | Grootte | Opmerking |
|---|---|---|
| Positionering printer | ~0,01 mm | Het verslag noemt 0,01 mm |
| Doorbuiging mount | 3–6 µm bij 20 N | FEM uit het verslag, verwaarloosbaar |
| FDM-kegeltop | 0,1–0,2 mm | Een geprinte top is afgerond; het zwaartepunt is wel goed te bepalen |
| Contactdetectie Z | ~0,01–0,05 mm | Bij de langzame zoeksnelheid; de vertraging van de loadcell bepaalt dit |
| Zwaartepunt camera | een paar pixels | Hangt af van A2 |
| Herhaalbaarheid wissel | onbekend | Takenplan §9: meten |

**Voorstel voor het doel:** XY beter dan 0,2 mm en Z beter dan 0,05 mm op het controlepunt F4.

---

## Aanbevelingen voor de kegels (`[fiducials]`)

- **Geef de top een ronde punt** (bol met een straal van ongeveer 1 mm) in plaats van een scherpe punt. Een scherpe punt kan het polymeer insnijden en geeft een onregelmatig fringe-patroon. Een bolletje geeft een Hertz-contact met **concentrische fringe-ringen**: makkelijk te centreren met de camera (B3b), en ook voor het tastend zoeken (B3c) geeft het een gladde top.
- Houd ze **5 mm hoog**, hoger dan de meeste testonderdelen, zodat de sensorbehuizing niets anders raakt.
- **Raak ze licht aan** (10–20 g): de kalibratie mag de kegels niet vervormen.

---

## Volgorde van bouwen

1. **Klaar (deze branch):** `machine_settings.toml` en `machine_settings.py`. De bestaande config leest daaruit.
2. **Logica zonder hardware**, met tests:
   - Z-vlak uit drie contacthoogtes;
   - `expect_z` per onderdeel;
   - de acceptatiecriteria van B5 (rood blokkeert);
   - het opslaan van B7.
3. **Kalibreer-knop in de path programmer**, eerst halfautomatisch: Z automatisch aanraken (B2), XY handmatig (B3a). Plus de controles van B0, waaronder de bewaking op verse loadcell-data.
4. **Camera:**
   - beeld in de calibrator (IDS op Windows, de macOS-backend op de Mac);
   - het fringe-zwaartepunt berekenen;
   - de routine voor de cameraschaal (A2).
5. **Automatisch XY** met de camera (B3b), met tastend zoeken (B3c) als reserve en controle.
6. **Vierde kegel F4** in `slicing/` (B6).
7. **Driftcontrole** tijdens het meten (C).

## Open vragen voor het team

- **Welke mount** wordt het: zijkant of fan mount V2? Dat bepaalt de afstand nozzle–sensor en het bereik.
- **Valt de camera-as samen met het contactpunt?** De camera kijkt door de draaidop, dus waarschijnlijk wel. Anders is er ook een afstand camera–contactpunt nodig.
- **Na de print zet de eindcode de X/Y-motoren uit** (`M84 X Y E`). Bij de wissel kan de kop dan verschuiven. De kalibratie vangt een vaste verschuiving op, maar een verschoven kop kan ook tegen de stops lopen. Opties: `M84 X Y` uit de eindcode halen, of na de wissel alleen X/Y homen. Dat laatste mag alleen als de firmware dat met de stops veilig doet.
- **Hoe stevig mag het polymeer de kegeltop raken?** Dat bepaalt de kalibratiekracht.

---

## Het instellingenbestand

**Ja, één bestand is een goed idee**, met een duidelijke grens:

| Hoort erin | Hoort er niet in |
|---|---|
| Waarden die van de **fysieke opstelling** afhangen en die het team bewust verandert: afstand nozzle–sensor, bereik met de stops, kegels, raster, krachtgrenzen, cameraschaal, acceptatiegrenzen | **Meetresultaten** per plaat of sessie: die gaan in `plate_NN_calibration.json` en de run-logs |
| | **Instellingen in de code** die niemand hoeft te veranderen (afstemming van `touch.py`, regexen) |
| | **Wachtwoorden en IP-adressen** (PrusaLink): die blijven lokaal in de app-instellingen |

**Waarom TOML?**
- Er kunnen opmerkingen bij, het leest als een formulier, en wijzigingen zijn goed te zien in git.
- Python leest het zonder extra bibliotheek (`tomllib`).
- Het bestand wordt bij het opstarten gecontroleerd. Een tikfout, een ontbrekende waarde of iets dat niet klopt (een kegel die niet in de rand past, krachtgrenzen in de verkeerde volgorde) geeft meteen een duidelijke foutmelding met sectie en sleutel. Je krijgt geen vreemd gedrag halverwege een meting.

**Een andere opstelling** (bijvoorbeeld een tweede printer of de fan mount) gebruikt een eigen bestand via `PRINTERPROJECT_SETTINGS=pad/naar/ander.toml`.
