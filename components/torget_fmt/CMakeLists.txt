# sv-SE-formatering (kommadecimal, hårt mellanslag, tickersplit) — ren C11,
# hosttestad med clang (test/run.sh). Skördad ur Solelkollens solglance-kärna
# när Tokenmätaren blev andra användaren (biblioteksregeln).
idf_component_register(
  SRCS "fmt_sv.c"
  INCLUDE_DIRS ".")
