# 2026-08-14 — OTA-kedjan verifierad ände till ände (övervakad boot)

## Setup

- Panel på Mac-USB (konsol via USB-Serial-JTAG; datahämtningar opålitliga
  på denna strömkälla — loggen är sanningen, glaset är det inte).
- Bootloader med CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE USB-flashad EN gång
  (fällan: sdkconfig genererad före defaults-raden ⇒ sju tidigare OTA:er
  bootade som UNDEFINED, grinden vilade och återöppningen kunde aldrig
  trigga — dömd via övervakad boot, fixad samma kväll).

## Bevisat i konsolloggen (OTA #9, v0.2.1-37-gd933d30 → ota_1)

```
I (403)  boot: Loaded app from partition at offset 0x520000
I (1071) boot-health: första boot på ota_1: hälsogrinden aktiv (8 s minimum, 15 s deadline)
I (1073) boot-health: bevis 0x01 på plats        (NVS)
I (1077) boot-health: bevis 0x02 på plats        (minne)
I (2605) boot-health: bevis 0x04 på plats        (display)
I (2815) boot-health: bevis 0x08 på plats        (UI)
I (2923) boot-health: bevis 0x10 på plats        (schemaläggare)
I (3058) ota-service: nyss uppdaterad avbild: underhållsfönstret återöppnas
I (9116) boot-health: avbilden godkänd: alla lokala bevis på plats
```

- PENDING_VERIFY-boot: JA (grinden aktiv — första gången på riktig OTA).
- Alla fem bevisen: JA, inom 3 s.
- Auto-reopen: JA vid 3,1 s — koden var frisk hela tiden; den väntade på
  en bootloader som faktiskt skriver PENDING_VERIFY.
- Godkännande via esp_ota_mark_app_valid_cancel_rollback: JA vid 9,1 s.
- Rollback-nätet är därmed ARMERAT från och med denna boot: en avbild som
  inte klarar grinden rullas tillbaka av bootloadern.

## Följdfynd under samma pass

- 17-teckenskapaciteten klippte git describe-versioner tyst ⇒ notisen
  fick aldrig veta (fixad: eget 32-bytesfält + parse-test).
- Pusharens hardkodade build/-default pekade på en övergiven diagnosbinär
  (fixad: nyaste build*/torget.bin vinner).
- Stale på glaset vid Mac-USB-drift är ett STRÖMSYMPTOM (WiFi-bursts
  brownoutar; servern frisk) — dokumenterat i agent-setup.
