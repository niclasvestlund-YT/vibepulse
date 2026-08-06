#include "torget_app.h"

#include "app_solelkollen.h"
#include "app_tokens.h"

/*
 * Appregistret: DEN här byggens appar, i launcherordning. Registret är
 * byggets konfiguration — vill du ha en annan skärm bygger du en annan binär
 * med ett annat register (en skärm = en binär, P25). Filen delas
 * byte-identiskt med simulatorn så bänken alltid visar samma appuppsättning
 * som hyllan.
 *
 * Lägga till en app: skriv en komponent som exporterar en torget_app_t,
 * inkludera dess header här och lägg in pekaren. Launchern gör resten.
 */

const torget_app_t *const torget_apps[] = {
  &solelkollen_app,
  &tokens_app,
};

const int torget_app_count =
  (int)(sizeof torget_apps / sizeof torget_apps[0]);
