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
if(EXISTS "$ENV{HOME}/Buddy/components/app_buddy")
  list(APPEND EXTRA_COMPONENT_DIRS "$ENV{HOME}/Buddy/components")
else()
  message(STATUS "Torget: ~/Buddy saknas — bygger utan Vibbe/Buddy (app 3)")
endif()
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(torget)
