#ifndef SOLGLANCE_GLANCE_H
#define SOLGLANCE_GLANCE_H

/* Mirrors /api/glance minus the transport fields. Shared by the parser, the
 * UI and both hosts (sim + ESP target). No LVGL, no libc surprises. */
typedef struct {
  double kr;
  double kr_per_hour;
  int w;
  int ore;
  int level; /* -1 cheap · 0 normal · 1 expensive */
  long long year_kr;
  long long total_kr;
  long long installation_kr;
  double payback_pct;
} sg_glance;

/* Mirrors /api/glance-sverige (P23): Sveriges avräknade solel, den nationella
 * syskonvyn. `day` är senast TROVÄRDIGT avräknade dygn (inte nödvändigtvis
 * igår — avräkningen mognar i dagar), och `share_pct` kan saknas i payloaden
 * (null) — då är has_share 0 och vyn visar streck, aldrig en hittad andel. */
typedef struct {
  double day_gwh;
  double share_pct;
  int has_share;
  char day[11]; /* "YYYY-MM-DD" */
} sg_sverige;

#endif
