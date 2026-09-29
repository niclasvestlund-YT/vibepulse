#ifndef APP_OPENPULSE_H
#define APP_OPENPULSE_H
#include "torget_app.h"
#include "model.h"
extern const torget_app_t openpulse_app;
void openpulse_apply(const op_snapshot *snapshot);
void openpulse_failed(void);
void openpulse_set_page(int page);
int openpulse_key_index(void);
void openpulse_net_start(void);
bool openpulse_layout_valid(void);
#endif
