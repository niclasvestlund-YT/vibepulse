if("$ENV{TORGET_OPENPULSE}" STREQUAL "ON")
  idf_component_register(SRCS "app.c" "model.c" "net.c" INCLUDE_DIRS "."
    PRIV_INCLUDE_DIRS "../.." REQUIRES torget_app cjson lvgl__lvgl PRIV_REQUIRES torget_net)
else()
  idf_component_register()
endif()
