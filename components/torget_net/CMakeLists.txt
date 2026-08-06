# Glance-mönstrets HTTP-klient: en begränsad GET, hela kroppen eller
# ingenting. Bara targetet — simulatorn läser fixtures från disk.
idf_component_register(
  SRCS "torget_http.c"
  INCLUDE_DIRS "."
  PRIV_REQUIRES esp_http_client esp-tls)
