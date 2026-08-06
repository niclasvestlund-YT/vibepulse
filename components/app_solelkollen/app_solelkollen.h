#ifndef APP_SOLELKOLLEN_H
#define APP_SOLELKOLLEN_H

#include "torget_app.h"

#include "glance.h"

/*
 * Solelkollen som Torget-app: fyra svepbara vyer (idag, året, återbetalt,
 * Sverige) ovanpå samma datakontrakt som webben — /api/glance och
 * /api/glance-sverige på solelkollen.se, oförändrade. Appen äger sin egen
 * hämttask (net.c, bara targetet); simulatorn matar apply-funktionerna
 * nedan med fixtures genom SAMMA parser (glance_parse.c).
 */

extern const torget_app_t solelkollen_app;

/* Ett lyckat /api/glance-svar. Snappar tickern (aldrig bakåt inom
 * öresbruset), stämplar färskhet och håller skärmen vaken när solen
 * producerar. Kallas under torget_ui_lock(). */
void solelkollen_apply_glance(const sg_glance *g);

/* Ett lyckat /api/glance-sverige-svar (settlement, egen kadens). */
void solelkollen_apply_sverige(const sg_sverige *s);

/* Hoppa till vy 0-3 utan animation — bänkens och BMP-dumparnas ratt. */
void solelkollen_show_view(int idx);

#endif
