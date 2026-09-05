# LVGL SDL-simulator för Torget. Bygg:
#   cmake -S sim -B sim/build -G Ninja && ninja -C sim/build
#   ./sim/build/torget-sim
cmake_minimum_required(VERSION 3.24)
project(torget-sim C)

set(CMAKE_C_STANDARD 11)

include(FetchContent)
# P20: samma minor som targetets BSP löser till (dependencies.lock) — en
# pixel som stämmer på Macen ska stämma på panelen. Vid BSP-bump: uppdatera här.
FetchContent_Declare(lvgl
  URL https://github.com/lvgl/lvgl/archive/refs/tags/v9.5.0.tar.gz
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE)

set(LV_CONF_PATH "${CMAKE_CURRENT_SOURCE_DIR}/lv_conf.h" CACHE STRING "" FORCE)
FetchContent_MakeAvailable(lvgl)

find_package(SDL2 REQUIRED)
# LVGL:s egen SDL-drivrutin inkluderar <SDL2/SDL.h> — biblioteket självt
# måste alltså se SDL:s headers, inte bara vår executable.
target_link_libraries(lvgl PUBLIC SDL2::SDL2)

# Samma filer som targetet bygger, minus apparnas net.c (simulatorn matar
# fixtures i stället) och minus main/ (värdlagret är den här filens main.c).
file(GLOB FONTS "${CMAKE_CURRENT_SOURCE_DIR}/../platform/fonts/*.c")

# Companion-apparna bor i sina EGNA repon och delas in harifran nar de finns
# utcheckade — exakt som targetet. En farsk klon utifran bygger bara
# VibePulse; registry.c grindas pa TORGET_HAVE_SOLELKOLLEN/TORGET_HAVE_BUDDY.
set(TORGET_SOLELKOLLEN_DIR "$ENV{HOME}/Solelkollen/components"
    CACHE PATH "Companion-katalog för Solelkollen")
set(TORGET_BUDDY_DIR "$ENV{HOME}/Buddy/components"
    CACHE PATH "Companion-katalog för Vibbe/Buddy")

if(EXISTS "${TORGET_SOLELKOLLEN_DIR}/app_solelkollen")
  set(SOLELKOLLEN_SRCS
    ${TORGET_SOLELKOLLEN_DIR}/app_solelkollen/app.c
    ${TORGET_SOLELKOLLEN_DIR}/app_solelkollen/glance_parse.c)
else()
  set(SOLELKOLLEN_SRCS "")
  message(STATUS "torget-sim: Solelkollen saknas — bygger utan den (companion-app)")
endif()

# Samma opt-in som targetet (frysläxan 2026-08-14): bänken ska visa exakt
# den appuppsättning panelen bygger, aldrig en app till bara för att
# katalogen råkar finnas.
option(TORGET_WITH_BUDDY "Bygg in Vibbe/Buddy (kräver ~/Buddy)" OFF)
if(TORGET_WITH_BUDDY AND EXISTS "${TORGET_BUDDY_DIR}/app_buddy")
  set(BUDDY_SRCS
    ${TORGET_BUDDY_DIR}/app_buddy/app.c
    ${TORGET_BUDDY_DIR}/app_buddy/buddy_soul.c
    ${TORGET_BUDDY_DIR}/app_buddy/buddy_face.c
    ${TORGET_BUDDY_DIR}/app_buddy/vibbe_sounds.c)
else()
  set(BUDDY_SRCS "")
  message(STATUS "torget-sim: Vibbe/Buddy är bortvald (TORGET_WITH_BUDDY=OFF)")
endif()
add_executable(torget-sim
  main.c
  ../platform/torget_ui.c
  ../platform/wifi_status_assets.c
  ../platform/boot_screen.c
  ../platform/settings_menu.c
  ../platform/button_arbitration.c
  ../main/registry.c
  ../components/app_tokens/app.c
  ../components/app_tokens/tokens_parse.c
  ../components/app_tokens/usage_presenter.c
  ../components/app_tokens/usage_screen.c
  ../components/app_tokens/agent_status_parse.c
  ../components/app_tokens/agent_usage.c
  ../components/app_tokens/agent_monitor_policy.c
  ../components/app_tokens/usage_live_policy.c
  ../components/app_tokens/agent_completion_policy.c
  ../components/app_tokens/needs_you_policy.c
  ../components/app_tokens/interaction_relay_policy.c
  ../components/app_tokens/agent_status_source_policy.c
  ../components/app_tokens/needs_you_send_policy.c
  ../components/app_tokens/agent_net_policy.c
  ../components/app_tokens/agent_monitor.c
  ../components/app_tokens/agent_assets.c
  ../components/app_tokens/github_status_parse.c
  ../components/app_tokens/project_star_popup_policy.c
  ../components/app_tokens/project_star_assets.c
  ../components/app_tokens/project_star_chime.c
  ../components/app_tokens/project_star_popup.c
  ../components/app_tokens/max_tracker_parse.c
  ../components/app_tokens/max_tracker_presenter.c
  ../components/torget_fmt/fmt_sv.c
  ../components/torget_ticker/ticker.c
  # OTA-ringen: ren LVGL utan ESP-beroenden — bänken bevisar dess raster.
  ../components/torget_ota/ota_ui.c
  # Wi-Fi-onboardingen: samma delade LVGL-lager som targetet visar.
  ../components/torget_wifi/wifi_slots.c
  # KEY3:s tidsregler och skiljedom — samma filer som targetet bygger.
  ../components/torget_ota/button_policy.c
  ../components/torget_wifi/wifi_qr_payload.c
  ../components/torget_wifi/wifi_setup_ui.c
  ../third_party/cjson/cJSON.c
  ${SOLELKOLLEN_SRCS}
  ${BUDDY_SRCS}
  ${FONTS})

# Simulatorn läser de riktiga fixture-filerna genom de delade parsrarna.
get_filename_component(FIXTURES_ABS "${CMAKE_CURRENT_SOURCE_DIR}/../sim-fixtures" ABSOLUTE)
target_compile_definitions(torget-sim PRIVATE
  FIXTURES_DIR="${FIXTURES_ABS}"
  TK_GITHUB_SCREEN_ENABLED=1
  TK_GITHUB_NOTIFICATIONS_ENABLED=1
  TK_GITHUB_SOUND_ENABLED=1)

target_include_directories(torget-sim PRIVATE
  "${CMAKE_CURRENT_SOURCE_DIR}"
  "${CMAKE_CURRENT_SOURCE_DIR}/../platform"
  "${CMAKE_CURRENT_SOURCE_DIR}/../components/app_tokens"
  "${CMAKE_CURRENT_SOURCE_DIR}/../components/torget_fmt"
  "${CMAKE_CURRENT_SOURCE_DIR}/../components/torget_ticker"
  "${CMAKE_CURRENT_SOURCE_DIR}/../components/torget_ota"
  "${CMAKE_CURRENT_SOURCE_DIR}/../components/torget_wifi")
if(SOLELKOLLEN_SRCS)
  target_include_directories(torget-sim PRIVATE
    "${TORGET_SOLELKOLLEN_DIR}/app_solelkollen")
  target_compile_definitions(torget-sim PRIVATE TORGET_HAVE_SOLELKOLLEN)
endif()
if(BUDDY_SRCS)
  target_include_directories(torget-sim PRIVATE
    "${TORGET_BUDDY_DIR}/app_buddy")
  target_compile_definitions(torget-sim PRIVATE TORGET_HAVE_BUDDY)
endif()
# m: presenterarnas lround/llround. På macOS bor libm i libSystem, så detta
# är en no-op där; på Linux (headless CI) är det länkfel utan.
target_link_libraries(torget-sim PRIVATE lvgl SDL2::SDL2 m)
