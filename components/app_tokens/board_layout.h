#pragma once
#include "display_geometry.h"
#include "vibepulse_layout.generated.h"
#ifdef TORGET_BOARD_191_TOUCH
#undef VP_SCREEN_H
#undef VP_QUOTA_Y
#undef VP_PERCENT_Y
#undef VP_BAR_Y
#undef VP_BAR_H
#undef VP_RESET_Y
#define VP_SCREEN_H 240
#define VP_QUOTA_Y 64
#define VP_PERCENT_Y 92
#define VP_BAR_Y 194
#define VP_BAR_H 12
#define VP_RESET_Y 96
#endif
