# Vendrad cJSON 1.7.18 (MIT) — samma parser på Macen som på kortet, annars
# är hosttesterna inte längre ett bevis om targetet. Kompileras EN gång här;
# apparna inkluderar headern med relativ sökväg.
idf_component_register(
  SRCS "cJSON.c"
  INCLUDE_DIRS ".")
