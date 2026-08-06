#include "rotation.h"

#include <math.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "bsp/esp-bsp.h"
#include "esp_log.h"
#include "esp_lv_adapter.h"
#include "qmi8658.h"

/* main.c äger panelhandtagen sedan displaystarten blev vår egen; BSP:ns
 * bsp_display_rotation_set läser statiska handles som aldrig sätts. */
esp_err_t torget_display_rotation_set(bsp_display_rotation_t rotation);

static const char *TAG = "rotation";

/* 5 Hz räcker gott för en pryl man vrider med händerna, och fem stabila
 * prover i rad = 1 s hysteres innan bilden vänder. */
#define POLL_MS        200
#define STABLE_SAMPLES 5

/* Orientering läses bara när panelen inte ligger platt: telefonprincipen.
 * Tre iterationer av trösklarna på riktig hårdvara 2026-08-06:
 *   1) inplane > 650 — missade bakåtlutad på stativ ("ibland"-felet)
 *   2) + z < 700   — frös i handen lutad mot ansiktet (såg ut som trasig,
 *      men var bara kvarhållet läge från förra vridningen)
 *   3) nu: z < 850 OCH inplane > 350 — 45°-handposer läses, helt platt
 *      (uppmätt liggande: z 974) fryser fortfarande, som en telefon på bordet.
 * Vid frys behålls senaste läget. Död zon mot diagonaler som förut. */
#define MIN_INPLANE_MG  350.0f
#define MAX_FLAT_Z_MG   850.0f
#define MAX_OFFAXIS_DEG 35.0f

/* MADCTL-cykeln för kvartsvarv. Boot-orienteringen är 0xA0 (ROTATE_270 i
 * BSP:ns numrering — panelen sitter roterad i kåpan, spec/hardware.md), så
 * r extra kvartsvarv från boot = cycle[(3 + r) % 4]. */
static const bsp_display_rotation_t CYCLE[4] = {
  BSP_DISPLAY_ROTATE_0, BSP_DISPLAY_ROTATE_90,
  BSP_DISPLAY_ROTATE_180, BSP_DISPLAY_ROTATE_270,
};

static qmi8658_dev_t s_imu;
static lv_indev_read_cb_t s_orig_read;
static volatile int s_rot;        /* 0-3: extra kvartsvarv från boot */

/* FAST kalibrering (självankringen revs 2026-08-06: den ankrade den pose
 * användaren råkade hålla vid boot och allt blev fel därefter).
 * SG_QUAD_UP = IMU-kvadranten när skärmen står rättvänt (uppmätt stående:
 * x -932, y 447 → 154° → kvadrant 2). SG_QUAD_DIR vänder vridriktningen om
 * hårdvarutestet visar spegelvända kvartsvarv. Ett fel i endera syns som
 * konstant fel läge respektive fel håll — en konstant var att justera. */
#define SG_QUAD_UP  1  /* kontrollerat 4-lägestest: med 3 blev ALLA lägen exakt upp och ner → offset 2 fel, riktningen rätt */
#define SG_QUAD_DIR -1 /* +1 gav spegelvända sidolägen (hårdvarutest 2026-08-06) */

/* Touchen roteras i MOTSATT led mot bilden, ett kvartsvarv per steg.
 * Vridriktningen mot IMU:n är empirisk — visar sig hårdvarutestet att
 * bilden vrider åt fel håll är detta konstanten att vända. */
#define TOUCH_CW 1

static void read_rotated(lv_indev_t *indev, lv_indev_data_t *data) {
  s_orig_read(indev, data);
  int r = s_rot & 3;
  for (int i = 0; i < r; i++) {
    int32_t x = data->point.x;
#if TOUCH_CW
    data->point.x = data->point.y;
    data->point.y = 479 - x;
#else
    data->point.x = 479 - data->point.y;
    data->point.y = x;
#endif
  }
}

/* Vinkeln i panelplanet → närmaste kvadrant (0-3), eller -1 utanför den
 * tillåtna dödzonen eller när enheten inte står upp. */
static int quad_of(float ax, float ay, float az) {
  float inplane = sqrtf(ax * ax + ay * ay);
  if (inplane < MIN_INPLANE_MG) return -1;   /* ingen riktning att läsa */
  if (fabsf(az) > MAX_FLAT_Z_MG) return -1;  /* ligger platt på bordet */

  float ang = atan2f(ay, ax) * 180.0f / (float)M_PI; /* -180..180 */
  if (ang < 0) ang += 360.0f;
  int quad = (int)lroundf(ang / 90.0f) % 4;
  float center = (float)quad * 90.0f;
  float diff = fabsf(ang - center);
  if (diff > 180.0f) diff = 360.0f - diff;
  return diff <= (90.0f - MAX_OFFAXIS_DEG) ? quad : -1;
}

static void apply_rotation(int r) {
  /* Adapterlåset stoppar pågående flush: MADCTL och QSPI-flöden får aldrig
   * flätas. Full invalidering efteråt — panelens adressmappning har bytts,
   * varje delvis uppdatering vore skräp. */
  if (esp_lv_adapter_lock(-1) != ESP_OK) return;
  torget_display_rotation_set(CYCLE[(3 + r) % 4]);
  s_rot = r;
  lv_obj_invalidate(lv_screen_active());
  esp_lv_adapter_unlock();
  ESP_LOGI(TAG, "roterade till läge %d", r);
}

static void rotation_task(void *arg) {
  (void)arg;
  float fx = 0, fy = 0, fz = 0; /* lågpass så ett hugg i bordet inte vrider */
  int cand = -1, held = 0;

  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(POLL_MS));

    float ax, ay, az;
    /* Komponentens header lovar read_accel_mg men källan levererar den inte;
     * read_accel ger mg när mps2-flaggan är av (default). */
    if (qmi8658_read_accel(&s_imu, &ax, &ay, &az) != ESP_OK) continue;
    fx += 0.3f * (ax - fx);
    fy += 0.3f * (ay - fy);
    fz += 0.3f * (az - fz);

    /* En gång i minuten: rå gravitation — kalibreringskvitto för P24.
     * Uppmätt 2026-08-06 liggande: x 73, y 427, z 974 mg → panelnormalen är
     * IMU-z, planet x/y, precis som antaget. */
    static int dbg;
    if (++dbg >= 300) {
      dbg = 0;
      ESP_LOGI(TAG, "gravitation: x %.0f, y %.0f, z %.0f mg", fx, fy, fz);
    }

    int quad = quad_of(fx, fy, fz);
    if (quad < 0) { cand = -1; held = 0; continue; }

    int want = ((SG_QUAD_DIR * (quad - SG_QUAD_UP)) % 4 + 4) % 4;
    if (want == s_rot) { cand = -1; held = 0; continue; }
    if (want == cand) {
      if (++held >= STABLE_SAMPLES) {
        ESP_LOGI(TAG, "kvadrant %d (%.0f/%.0f mg) → läge %d", quad, fx, fy, want);
        apply_rotation(want);
        cand = -1;
        held = 0;
      }
    } else {
      cand = want;
      held = 1;
    }
  }
}

void sg_rotation_start(lv_indev_t *touch) {
  if (bsp_i2c_init() != ESP_OK) {
    ESP_LOGW(TAG, "ingen I2C-buss, rotationen avstängd");
    return;
  }
  /* Adressen var overifierad i reconen (SA0-benet avgör): prova båda. */
  if (qmi8658_init(&s_imu, bsp_i2c_get_handle(), QMI8658_ADDRESS_LOW) != ESP_OK &&
      qmi8658_init(&s_imu, bsp_i2c_get_handle(), QMI8658_ADDRESS_HIGH) != ESP_OK) {
    /* Utan IMU står bilden stilla i bootläget — degradering, inte fel. */
    ESP_LOGW(TAG, "QMI8658 svarar inte (0x6A/0x6B), rotationen avstängd");
    return;
  }
  qmi8658_set_accel_range(&s_imu, QMI8658_ACCEL_RANGE_2G);
  qmi8658_set_accel_odr(&s_imu, QMI8658_ACCEL_ODR_LOWPOWER_21HZ);
  qmi8658_enable_accel(&s_imu, true);
  s_imu.accel_unit_mps2 = false; /* mg; header-setterns funktion finns inte i källan */

  if (touch) {
    s_orig_read = lv_indev_get_read_cb(touch);
    if (s_orig_read) lv_indev_set_read_cb(touch, read_rotated);
  }

  xTaskCreate(rotation_task, "rotation", 3072, NULL, 3, NULL);
  ESP_LOGI(TAG, "IMU igång (fast kalibrering: kvadrant %d = rättvänt)", SG_QUAD_UP);
}
