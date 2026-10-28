idf_component_register(SRCS "main.c" INCLUDE_DIRS "." REQUIRES epaper driver esp_timer esp_hw_support
 EMBED_FILES "boot.raw" "offline.raw")
