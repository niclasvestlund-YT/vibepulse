# Den lokala tickern: extrapolerar ett värde mellan hämtningarna utifrån en
# serverlevererad takt, på enhetens monotona klocka (serverns tid används
# aldrig som tidsbas). Ren C11, hosttestad. Skördad ur solglance när
# Tokenmätaren blev andra tickande appen.
idf_component_register(
  SRCS "ticker.c"
  INCLUDE_DIRS ".")
