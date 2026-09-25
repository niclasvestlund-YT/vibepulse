# CMake's default C archive rules use the host ar/ranlib on macOS. The
# ESP-IDF bootloader is a separate CMake project and needs the Xtensa versions.
find_program(_torget_xtensa_ar NAMES xtensa-esp32s3-elf-ar REQUIRED)
find_program(_torget_xtensa_ranlib NAMES xtensa-esp32s3-elf-ranlib REQUIRED)
set(CMAKE_AR "${_torget_xtensa_ar}")
set(CMAKE_RANLIB "${_torget_xtensa_ranlib}")
