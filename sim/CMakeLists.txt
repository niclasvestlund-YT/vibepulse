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
add_executable(torget-sim
  main.c
  ../platform/torget_ui.c
  ../main/registry.c
  ../components/app_solelkollen/app.c
  ../components/app_solelkollen/glance_parse.c
  ../components/app_tokens/app.c
  ../components/app_tokens/tokens_parse.c
  ../components/app_tokens/agent_status_parse.c
  ../components/app_tokens/agent_usage.c
  ../components/app_tokens/agent_monitor_policy.c
  ../components/app_tokens/agent_net_policy.c
  ../components/app_tokens/agent_monitor.c
  ../components/app_tokens/agent_assets.c
  ../components/torget_fmt/fmt_sv.c
  ../components/torget_ticker/ticker.c
  ../third_party/cjson/cJSON.c
  ${FONTS})

# Simulatorn läser de riktiga fixture-filerna genom de delade parsrarna.
get_filename_component(FIXTURES_ABS "${CMAKE_CURRENT_SOURCE_DIR}/../sim-fixtures" ABSOLUTE)
target_compile_definitions(torget-sim PRIVATE FIXTURES_DIR="${FIXTURES_ABS}")

target_include_directories(torget-sim PRIVATE
  "${CMAKE_CURRENT_SOURCE_DIR}"
  "${CMAKE_CURRENT_SOURCE_DIR}/../platform"
  "${CMAKE_CURRENT_SOURCE_DIR}/../components/app_solelkollen"
  "${CMAKE_CURRENT_SOURCE_DIR}/../components/app_tokens"
  "${CMAKE_CURRENT_SOURCE_DIR}/../components/torget_fmt"
  "${CMAKE_CURRENT_SOURCE_DIR}/../components/torget_ticker")
target_link_libraries(torget-sim PRIVATE lvgl SDL2::SDL2)
