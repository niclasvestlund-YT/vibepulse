#include <stdio.h>
#include <string.h>

#include "../components/app_tokens/usage_presenter.h"

static int failures = 0;

static void check(const char *what, int condition) {
  if (!condition) {
    printf("FAIL %s\n", what);
    failures++;
  }
}

static tk_limit limit(double pct, int reset_min, double delta) {
  tk_limit out = {0};
  out.pct = pct;
  out.reset_min = reset_min;
  out.delta_pct = delta;
  out.has_pct = 1;
  out.has_reset = 1;
  out.has_delta = 1;
  return out;
}

int main(void) {
  tk_tokens tokens = {0};
  tokens.claude_model_week = limit(73, 3120, 3);
  tokens.claude_week = limit(47, 300, 7);
  tokens.claude_session = limit(21, 80, 11);
  tokens.has_claude_model_week_label = 1;
  snprintf(tokens.claude_model_week_label,
           sizeof tokens.claude_model_week_label, "FABLE · VECKA");

  usage_provider_view view = {0};
  usage_presenter_build_provider(&tokens, USAGE_PROVIDER_CLAUDE, 0, &view);
  check("Claude har två kort", view.card_count == 2);
  check("modellveckan ligger fast överst",
        view.cards[0].kind == USAGE_CARD_MODEL_WEEK &&
        strcmp(view.cards[0].label, "FABLE · VECKA") == 0);
  check("allveckan börjar underst",
        view.cards[1].kind == USAGE_CARD_ALL_WEEK &&
        strcmp(view.cards[1].label, "ALLA · VECKA") == 0);
  check("stor heltalsprocent", strcmp(view.cards[0].pct_text, "73%") == 0);
  check("dagsdelta har exakt copy",
        strcmp(view.cards[0].delta_text, "+3% IDAG") == 0);
  check("reset har kompakt copy",
        strcmp(view.cards[1].reset_text, "NOLLAS OM 5 H 00") == 0);
  check("reset har separat liten kvar-rad",
        strcmp(view.cards[0].reset_short_text, "2 D 4 H KVAR") == 0);
  check("långa resetfönster använder dygn även på nedersta raden",
        strcmp(view.cards[0].reset_text, "NOLLAS OM 2 D 4 H") == 0);

  usage_presenter_build_provider(&tokens, USAGE_PROVIDER_CLAUDE, 6999, &view);
  check("allveckan ligger kvar före sju sekunder",
        view.cards[1].kind == USAGE_CARD_ALL_WEEK);
  usage_presenter_build_provider(&tokens, USAGE_PROVIDER_CLAUDE, 7000, &view);
  check("femtimmarskortet visas vid sju sekunder",
        view.cards[1].kind == USAGE_CARD_FIVE_HOURS &&
        strcmp(view.cards[1].label, "5 TIMMAR") == 0);
  check("timdelta har exakt copy",
        strcmp(view.cards[1].delta_text, "+11% SENASTE H") == 0);
  usage_presenter_build_provider(&tokens, USAGE_PROVIDER_CLAUDE, 14000, &view);
  check("rotationen börjar om efter fjorton sekunder",
        view.cards[1].kind == USAGE_CARD_ALL_WEEK);

  tk_tokens unnamed = {0};
  unnamed.claude_model_week = limit(73, 850, 3);
  unnamed.claude_week = limit(47, 300, 7);
  unnamed.claude_session = limit(21, 80, 11);
  usage_presenter_build_provider(
      &unnamed, USAGE_PROVIDER_CLAUDE, 7000, &view);
  check("generisk modellquota blir aldrig Fable",
        view.cards[0].kind == USAGE_CARD_ALL_WEEK &&
        strcmp(view.cards[0].label, "ALLA · VECKA") == 0);
  check("sessionen kan ligga som andra kort utan rotation",
        view.cards[1].kind == USAGE_CARD_FIVE_HOURS);

  tk_tokens codex = {0};
  codex.codex_week = limit(35, 2210, 5);
  codex.codex_session = limit(80, 60, 10);
  usage_presenter_build_provider(
      &codex, USAGE_PROVIDER_CODEX, 7000, &view);
  check("Codex visar ett enda stort veckokort", view.card_count == 1);
  check("Codex visar inte sessionsquotan",
        view.cards[0].kind == USAGE_CARD_ALL_WEEK &&
        strcmp(view.cards[0].pct_text, "35%") == 0);

  tk_tokens missing = {0};
  usage_presenter_build_provider(
      &missing, USAGE_PROVIDER_CODEX, 0, &view);
  check("saknad quota är streck", strcmp(view.cards[0].pct_text, "–") == 0);
  check("saknad quota förklaras utan hittepåvärde",
        strcmp(view.cards[0].reset_text, "QUOTA SAKNAS") == 0);
  check("saknat delta är tomt", view.cards[0].delta_text[0] == '\0');

  if (failures == 0) {
    printf("OK: alla usage-presentertester gröna\n");
    return 0;
  }
  printf("%d test föll\n", failures);
  return 1;
}
