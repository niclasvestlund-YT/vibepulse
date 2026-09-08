#ifndef APP_TOKENS_CONFIG_H
#define APP_TOKENS_CONFIG_H

#ifdef ESP_PLATFORM
#include "secrets.h"
#endif

/*
 * Reläets adresser, härledda ur EN valfri bas i secrets.h.
 *
 * Reläet är en brevlåda på internet dit tjänsten LÄGGER sina färdiga
 * siffror, så panelen kan hämta dem utan att dela nät med värden. Utan
 * TK_VIBEPULSE_RELAY_URL blir varje adress NULL och hämtningarna beter sig
 * exakt som förut — LAN eller ingenting.
 *
 * GRÄNSEN, medveten och testad: reläet bär SIFFROR, aldrig AKTIVITET.
 * Kvot, burn rate, Max Tracker och GitHub går den vägen. Agentstatus och
 * Needs You gör det INTE — de bär projektnamn, frågetexter och kommandon,
 * och de lämnar aldrig LAN:et. Enhetsnyckelns svarsväg är av samma skäl
 * LAN-only: panelen kan svara på en fråga hemma, aldrig via en brevlåda.
 */
#ifdef TK_VIBEPULSE_RELAY_URL
#define TK_TOKENS_RELAY_URL      TK_VIBEPULSE_RELAY_URL "/api/tokens"
#define TK_MAX_TRACKER_RELAY_URL TK_VIBEPULSE_RELAY_URL "/api/max-tracker"
#define TK_GITHUB_RELAY_URL      TK_VIBEPULSE_RELAY_URL "/api/github"
#else
#define TK_TOKENS_RELAY_URL      NULL
#define TK_MAX_TRACKER_RELAY_URL NULL
#define TK_GITHUB_RELAY_URL      NULL
#endif

/* Old secrets.h files lack this macro: retain their analytics on upgrade.
 * The fresh-install template explicitly sets 0. NVS choices override defaults. */
#ifndef TK_LABS_ANALYTICS_DEFAULT
#define TK_LABS_ANALYTICS_DEFAULT 1
#endif
#if TK_LABS_ANALYTICS_DEFAULT != 0 && TK_LABS_ANALYTICS_DEFAULT != 1
#error "TK_LABS_ANALYTICS_DEFAULT must be 0 or 1"
#endif

/* The GitHub page and star popup are deliberately independent. A fresh clone
 * starts with both off. These macros seed LABS on the first boot only. */
#ifndef TK_GITHUB_SCREEN_ENABLED
#define TK_GITHUB_SCREEN_ENABLED 0
#endif

#ifndef TK_GITHUB_NOTIFICATIONS_ENABLED
#define TK_GITHUB_NOTIFICATIONS_ENABLED 0
#endif

/* Sound is a third, independent opt-in. It still requires a platform backend
 * that has passed the display-DMA and physical-speaker gates. */
#ifndef TK_GITHUB_SOUND_ENABLED
#define TK_GITHUB_SOUND_ENABLED 0
#endif

#if TK_GITHUB_SCREEN_ENABLED != 0 && TK_GITHUB_SCREEN_ENABLED != 1
#error "TK_GITHUB_SCREEN_ENABLED must be 0 or 1"
#endif

#if TK_GITHUB_NOTIFICATIONS_ENABLED != 0 && \
    TK_GITHUB_NOTIFICATIONS_ENABLED != 1
#error "TK_GITHUB_NOTIFICATIONS_ENABLED must be 0 or 1"
#endif

#if TK_GITHUB_SOUND_ENABLED != 0 && TK_GITHUB_SOUND_ENABLED != 1
#error "TK_GITHUB_SOUND_ENABLED must be 0 or 1"
#endif

#endif
