#!/bin/zsh
# Flashar Torget (med Vibbe ombord) utan knappdans — samma fönsterfångare som
# ~/Buddy/tools/flasha.sh: när Vibbe-appen kör äger TinyUSB USB-kontakten och
# serieporten finns bara ~2-3 s vid boot innan ljudnavet startar. Skriptet
# ligger på lur och kör esptool i det fönstret.
#
# Användning:  ./tools/flasha.sh   och dra sedan ur+i USB-kabeln när den ber.
# Fallback: håll BOOT medan kabeln sätts i (skärmen förblir svart).

source ~/esp/esp-idf/export.sh >/dev/null 2>&1
cd "$(dirname "$0")/../build" || { echo "bygget saknas — kör idf.py build först"; exit 1; }

echo "Väntar på porten... dra ur och sätt i USB-kabeln NU (inga knappar behövs)."
while true; do
  PORT=$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)
  if [ -n "$PORT" ]; then
    echo "Port: $PORT — flashar..."
    if python3 -m esptool --chip esp32s3 -p "$PORT" -b 460800 \
         --before default_reset --after hard_reset write_flash @flash_args; then
      echo ""
      echo "KLART — Torget bootar med alla appar (KEY3 växlar; Vibbe är en av dem)."
      exit 0
    fi
    echo "Missade fönstret — dra ur och sätt i kabeln igen (eller håll BOOT)."
    sleep 1
  fi
  sleep 0.2
done
