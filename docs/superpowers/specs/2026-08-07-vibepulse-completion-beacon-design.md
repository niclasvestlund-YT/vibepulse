# VibePulse: klarsignal för Claude Code och Codex

**Datum:** 2026-08-07
**Status:** vald design, inväntar användarens granskning före implementation

## Varför detta tillägg finns

VibePulse ska gå att förstå på avstånd. Den ordinarie vyn prioriterar därför
fortfarande quota och stora procentsiffror när en agent arbetar. När ett jobb
blir klart behöver apparaten däremot tillfälligt bli en tydlig signal: skärmen
pulserar i agentens riktiga färg, ett kort pip hörs och en stor klarsida visar
vilken agent och vilket projekt som är färdigt.

Tillägget ersätter inte statusmaskinen i
`2026-08-06-agentmonitor-design.md`. Det preciserar presentationen för
`working` och `done`, lokal kvittens samt samtidiga jobb.

## Övervägda lösningar

1. **Skärmen som färgsignal + kort pip (vald).** Hela AMOLED-panelen gör två
   långsamma färgpulser, därefter hörs ett pip och en lugn klarsida visas.
   Detta syns på avstånd och använder hårdvara som redan finns.
2. **Endast en lysande kant runt usage-vyn.** Quotan är synlig hela tiden,
   men avslutet är för lätt att missa från andra sidan rummet.
3. **Separat fysisk statuslampa.** Tydligast som ren notifiering, men kortet
   har ingen separat styrbar RGB-lampa eller backlight-GPIO. AMOLED-panelen
   är därför den fysiska ljussignalen.

## Ordinarie vy medan agenten arbetar

- Den stora quota-procenten och mätaren är huvudinformation. Ingen separat
  helskärm med ordet `JOBBAR` tar över medan arbetet pågår.
- En 72--84 px hög statusrad längst ned visar den riktiga providerikonen,
  aktivitet och projekt utan att minska huvudprocenten.
- Aktivitet visas med rörelse, inte bara ordet `TÄNKER`:
  - Codex-molnet andas högst två bildpunkter och terminalmarkören blinkar.
  - Claude-karaktären rör sig högst två bildpunkter och tre aktivitetsstaplar
    går i en lugn vänster--mitten--höger-våg.
- Aktiv loop är 6--8 fps och ingen blinkning är snabbare än 3 Hz.
- Om både Claude Code och Codex arbetar delas statusraden i två lika fält.
  Båda ikonerna rör sig oberoende.
- Om flera jobb från samma provider arbetar visas exempelvis
  `CODEX · 3 JOBBAR`. Projektet för det mest angelägna jobbet visas under.
  Prioritet är `waiting`, `error`, `working`, `done`, `idle`.

## Klarsignalens tidslinje

Ett nytt, tidigare ovisat `done`-event startar följande sekvens:

1. **0--2,4 s:** två lugna helskärmspulser mellan svart och providerfärgen.
   Det är en mjuk in- och uttoning, inte ett stroboskop.
2. **2,4 s:** ett kort providerljud spelas en gång.
3. **2,4--10 s:** den statiska klarsidan ligger kvar. Den visar provider,
   riktig ikon, stor text `KLAR` och verkligt projektnamn. Projektet
   `TORGET` är aldrig hårdkodat.
4. **Efter 10 s:** usage-vyn kommer tillbaka automatiskt. Statusraden visar
   fortsatt `KLAR · <PROJEKT>` tills eventet kvitteras eller ersätts av ny
   status. På så sätt försvinner inte informationen om användaren var borta.

Ett tryck var som helst på klarsidan kvitterar det aktuella eventet lokalt och
tar bort sidan omedelbart. Långtryck fortsätter öppna Torgets launcher. En
kvittens godkänner aldrig något i Claude Code eller Codex och skickar inget
till datorn.

## Providerutseende

### Codex

- Den riktiga källbildens moln och vita `>`/`_` används; ingen spökikon eller
  ungefärlig vektor ersätter asseten.
- Molnets originalgradient bevaras från cirka `#ACA9FF` upptill till
  `#3D48FF` nedtill. Nuvarande platta omfärgning med `#6F78FF` används inte
  på loggan.
- Helskärmspulsen använder den djupa originalkulören `#3D48FF`; statisk text
  är vit. Den riktiga gradientloggan ligger på en mörk yta så att dess nedre
  blå del inte försvinner mot bakgrunden.
- Ljud: två korta, rena stigande toner. Total ljudtid är under 350 ms.

### Claude Code

- Samma layout, tider, touchbeteende och informationshierarki som Codex.
- Den riktiga vita pixelkaraktären används på Claudes korallfärg `#D97757`.
  Ingen emoji eller generisk AI-symbol används.
- Helskärmspulsen är korall och statisk text är vit.
- Ljud: en kort varm dubbelton med samma maximala ljudtid som Codex men en
  annan tonföljd, så providern går att känna igen utan att se skärmen.

Första leveransen använder pip, inte syntetiskt tal. Röstfraser kan läggas
ovanpå samma eventkö senare utan att ändra status- eller UI-kontraktet.

## Flera samtidiga jobb

Tokenservern ska exponera en begränsad lista av färska jobb per provider i
stället för att endast skriva över en enda providerstatus. Varje jobb har
fortsatt endast sekretessbegränsade fält: opakt `task_id`, opakt `event_id`,
provider, state, kontrollerad aktivitet, projektnamn och uppdateringstid.

- Enheten behöver bara de fyra mest angelägna jobben per provider samt totalt
  antal aktiva jobb. Äldre, mindre angelägna poster räknas men skickas inte.
- Ett unikt `event_id` får skapa högst en visuell klarsignal och ett ljud.
- Om flera jobb blir klara nära varandra läggs deras klarsidor i FIFO-kö.
- Tryck kvitterar endast sidan som visas; nästa köade sida visas direkt.
- Om andra jobb fortfarande arbetar står exempelvis `2 JOBBAR` diskret längst
  ned på klarsidan. Deras status och animation fortsätter efter återgången.
- Ett klart Torget-jobb får aldrig dölja att Buddy, Solelkollen eller ett annat
  projekt fortfarande arbetar.

## Ljud och hårdvarugräns

Kortet har ES8311, högtalarväg och PA-enable på GPIO46. Ljud spelas från en
egen liten FreeRTOS-kö och får aldrig köras under UI-låset. DMA-buffertar
allokeras en gång ur internminnet. Ljudet är av som säkert fallbackläge om
kodekinit misslyckas; den visuella signalen fungerar ändå.

Eventdeduplicering sparas i NVS så samma pip inte spelas igen efter omstart.
Ljud kan stängas av med den befintligt planerade 44 x 44 px-högtalarknappen.
Standardvolymen är låg.

Panelen saknar separat backlight-GPIO. Färgpulsen görs därför i LVGL; den
globala panelstyrkan ändras inte upp och ned som del av animationen. Detta
undviker att nattläge och andra appar påverkas.

## Fel- och säkerhetsbeteende

- Tystnad eller en gammal `working`-lease får aldrig fabricera `done`.
- Trasig eller gammal status ger varken klarsida eller pip.
- Om projektnamn saknas visas bara provider och `KLAR`.
- Om ljudkön är full tappas det äldsta icke-kritiska ljudet; UI:t blockeras
  aldrig.
- Om en ny `waiting`- eller `error`-händelse kommer under en klarsida får den
  högre prioritet efter pågående mjuka puls, utan snabb dubbelblinkning.

## Verifiering

### Hosttester

- Arbetsraden väljer rätt antal, projekt och prioritetsjobb.
- Två providers kan vara aktiva samtidigt.
- Flera `done`-event köas i rätt ordning och dedupliceras med `event_id`.
- Tryck kvitterar bara aktuellt event; långtryck öppnar launchern.
- Autoåtergång sker efter 10 s och lämnar en persistent klarstatus.
- Codecfel påverkar inte den visuella signalen.

### Simulator

- Dumpa Codex och Claude Code i `working`, första färgpuls, statisk `KLAR`,
  två samtidiga providers och två köade klarsidor.
- Jämför den statiska klarsidan med vald mockup i exakt 480 x 480.
- Visa simulatorbilderna för användaren innan targetflash.

### Fysisk AMOLED

- Kontrollera från 2--3 meters avstånd att provider, `KLAR` och huvudprocent
  går att identifiera.
- Kontrollera att två långsamma pulser känns som signal, inte flimmer.
- Kontrollera pipets volym och att Codex och Claude går att skilja åt.
- Kontrollera touchkvittens, 10 s autoåtergång, KEY3, långtryck, rotation,
  nattläge och att animationen inte blockerar nätverk eller rendering.

## Leveransordning

1. Utöka statuskontrakt och tester för flera samtidiga jobb.
2. Implementera den usage-prioriterade arbetsraden och dess animationer.
3. Implementera klarkö, touchkvittens och de båda statiska klarsidorna.
4. Visa 480 x 480-simulatorbilder och justera layouten.
5. Gör en fysisk AMOLED-titt av den statiska overlayn.
6. Lägg till de två långsamma färgpulserna.
7. Lägg till codecinit, providerpip och NVS-deduplicering.
8. Kör full host-, simulator-, target- och fysisk verifiering.
