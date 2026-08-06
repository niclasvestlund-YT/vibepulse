# Solelkollen som Torget-app: vyerna + datakontraktet mot solelkollen.se
# (oförändrat) + appens egen hämttask. app.c och glance_parse.c delas
# byte-identiskt med simulatorn; net.c finns bara i det här bygget.
idf_component_register(
  SRCS "app.c" "glance_parse.c" "net.c"
  INCLUDE_DIRS "."
  REQUIRES torget_app torget_fmt torget_ticker cjson
  # secrets.h bor i repytroten (gitignorad), därav "../..".
  PRIV_INCLUDE_DIRS "../.."
  PRIV_REQUIRES torget_net)

# Fonterna deklareras extern och länkas ur main-komponenten; LVGL-headern
# heter "lvgl.h" på targetets sökväg (se main/CMakeLists.txt).
target_compile_definitions(${COMPONENT_LIB} PRIVATE LV_LVGL_H_INCLUDE_SIMPLE)
