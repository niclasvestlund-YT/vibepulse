# Torget — ESP-IDF-projektet för skärmplattformen. Bygg (efter
# `. ~/esp/esp-idf/export.sh`):
#   idf.py set-target esp32s3 && idf.py build
#   idf.py -p /dev/cu.usbmodem* flash monitor
#
# Simulatorn under sim/ är ett HELT eget CMake-projekt och byggs som förr.
# Det som delas mellan dem är platform/, components/app_* (utom net.c) och
# kärnkomponenterna — samma kod på Macen som på hyllan, per design.
cmake_minimum_required(VERSION 3.16)
# third_party/ innehåller vendrade komponenter (cjson) som apparna delar.
# Vibbe (app_buddy) bor i sitt eget repo (~/Buddy) och delas in härifrån —
# EN källa, ingen kopiedrift (inflyttsbeslutet 2026-08-07: "alla tre ska bo
# där"). Utan ~/Buddy (en färsk klon utifrån) byggs plattformen med två
# appar — registry.c grindas på TORGET_HAVE_BUDDY.
set(EXTRA_COMPONENT_DIRS third_party)

# Vibbe kräver UTTRYCKLIG avsikt sedan frysjakten 2026-08-14: dess ljud- och
# USB-stack äter av samma interna DMA-minne som panelflushen lever på, och
# med OTA-grundens ~12 kB extra bootlast föll största sammanhängande
# DMA-block under flushens 11 520 B — varje flush dog i NO_MEM och glaset
# frös på sista bilden medan resten av systemet levde vidare (obducerat via
# seriekonsolen: co5300 "send color data failed" i evig följd). Dessutom
# gömde TinyUSB konsolen exakt när den behövdes. Vibbe siktar på egen
# hårdvara; på VibePulse-panelen byggs den bara in med -DTORGET_WITH_BUDDY=ON
# och en medvetet omprövad minnesbudget.
option(TORGET_WITH_BUDDY "Bygg in Vibbe/Buddy (kräver ~/Buddy och medveten minnesbudget)" OFF)

# Companion-appar bor i sina EGNA repon och är inte del av det här bygget om
# de inte finns utcheckade. En färsk klon utifrån bygger bara VibePulse —
# ingen ska få Solelkollen eller Vibbe på skärmen utan att ha bett om det.
# Sökvägarna kan pekas om med -DTORGET_SOLELKOLLEN_DIR / -DTORGET_BUDDY_DIR.
set(TORGET_SOLELKOLLEN_DIR "$ENV{HOME}/Solelkollen/components"
    CACHE PATH "Companion-katalog för Solelkollen")
set(TORGET_BUDDY_DIR "$ENV{HOME}/Buddy/components"
    CACHE PATH "Companion-katalog för Vibbe/Buddy")

# main/CMakeLists.txt måste läsa dessa i ESP-IDF:s early-expansion (där
# REQUIRES bestäms). Dit når varken cache- eller normalvariabler härifrån —
# bara miljön överlever. Spegla de UPPLÖSTA värdena (inkl. ev. -D-override) till
# miljön så att main grindar sitt REQUIRES på exakt samma sökväg som grindar
# EXTRA_COMPONENT_DIRS nedan. Utan detta sätts TORGET_HAVE_* men komponentens
# include-katalog dras aldrig in, och registry.c faller på app_solelkollen.h.
set(ENV{TORGET_SOLELKOLLEN_DIR} "${TORGET_SOLELKOLLEN_DIR}")
# Tom spegel när Vibbe är bortvald: main/ grindar på $ENV{}-sökvägen, och en
# tom sträng gör båda expansionspassen eniga om att Buddy inte finns — även
# om skalet råkar ha TORGET_BUDDY_DIR exporterad sedan tidigare.
if(TORGET_WITH_BUDDY)
  set(ENV{TORGET_BUDDY_DIR} "${TORGET_BUDDY_DIR}")
else()
  set(ENV{TORGET_BUDDY_DIR} "")
endif()

if(EXISTS "${TORGET_SOLELKOLLEN_DIR}/app_solelkollen")
  list(APPEND EXTRA_COMPONENT_DIRS "${TORGET_SOLELKOLLEN_DIR}")
else()
  message(STATUS "Torget: Solelkollen saknas — bygger utan den (companion-app)")
endif()
if(TORGET_WITH_BUDDY AND EXISTS "${TORGET_BUDDY_DIR}/app_buddy")
  list(APPEND EXTRA_COMPONENT_DIRS "${TORGET_BUDDY_DIR}")
elseif(TORGET_WITH_BUDDY)
  message(STATUS "Torget: TORGET_WITH_BUDDY=ON men ~/Buddy saknas — bygger utan Vibbe/Buddy")
else()
  message(STATUS "Torget: Vibbe/Buddy är bortvald (TORGET_WITH_BUDDY=OFF, frysläxan 2026-08-14)")
endif()
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(torget)

# sdkconfig.defaults only seeds a new sdkconfig; it does not update an old
# generated file. A stale 96 KiB LVGL pool wedges the LVGL task in its default
# out-of-memory assert while rendering the 164 px quota glyphs. Refuse to
# produce that image instead of discovering the mismatch on the glass.
include("${CMAKE_CURRENT_SOURCE_DIR}/cmake/torget_lvgl_memory_guard.cmake")
torget_require_lvgl_pool("${CONFIG_LV_MEM_SIZE_KILOBYTES}")
include("${CMAKE_CURRENT_SOURCE_DIR}/cmake/torget_lvgl_qrcode_guard.cmake")
torget_require_lvgl_qrcode("${CONFIG_LV_USE_QRCODE}")
# Crash and log evidence (OBS-02/03/28/35): an old generated sdkconfig would
# silently drop the coredump writer, the LVGL warnings and the task watchdog.
include("${CMAKE_CURRENT_SOURCE_DIR}/cmake/torget_diagnostics_guard.cmake")
torget_require_diagnostics(
  "${CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH}"
  "${CONFIG_ESP_COREDUMP_DATA_FORMAT_ELF}"
  "${CONFIG_LOG_MAXIMUM_EQUALS_DEFAULT}"
  "${CONFIG_ESP_TASK_WDT_INIT}"
  "${CONFIG_LV_USE_LOG}")

# LVGL:s minnespool till PSRAM (frysfix 2026-08-16, se docs/lessons.md och
# main/lv_psram_pool.c). LVGL:s inbyggda allokator lägger annars sin
# LV_MEM_SIZE-pool i internt BSS; på panelen tryckte det största
# sammanhängande DMA-blocket under panelflushens 11 520 B → flushen dog i
# NO_MEM och glaset frös (samma klass som frysläxan 2026-08-14, men annan
# svältkälla). Vi ber LVGL ta poolen ur PSRAM via LV_MEM_POOL_ALLOC; det
# interna heapet får då tillbaka ~poolens storlek som sammanhängande block.
# lv_psram_pool.h/.c bor i main/ (därav include-katalogen); symbolen löses i
# den gemensamma länkgruppen.
# Bara en OBJEKT-lik include-def (CMake släpper funktionslika -D:n); själva
# LV_MEM_POOL_ALLOC-makrot bor i main/lv_psram_pool.h och pekar mot
# heap_caps_malloc (PSRAM + 8BIT, INTE DMA — se headern). Include-katalogen gör
# att lvgl-komponenten hittar headern.
idf_component_get_property(lvgl_lib lvgl__lvgl COMPONENT_LIB)
target_compile_definitions(${lvgl_lib} PRIVATE
  "LV_MEM_POOL_INCLUDE=\"lv_psram_pool.h\"")
target_include_directories(${lvgl_lib} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/main")
