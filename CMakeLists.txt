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
set(EXTRA_COMPONENT_DIRS third_party)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(torget)
