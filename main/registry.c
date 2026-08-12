#include "torget_app.h"

#include "app_solelkollen.h"
#include "app_tokens.h"

/* Vibbe/Buddy bor i ~/Buddy (companion-repot). Byggena sätter
 * TORGET_HAVE_BUDDY när det är utcheckat; utan det byggs registret med två
 * appar så en färsk klon utifrån alltid går att bygga. */
#ifdef TORGET_HAVE_BUDDY
#include "app_buddy.h"
#endif

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
#ifdef TORGET_HAVE_BUDDY
  &vibbe_app,
#endif
};

const int torget_app_count =
  (int)(sizeof torget_apps / sizeof torget_apps[0]);
