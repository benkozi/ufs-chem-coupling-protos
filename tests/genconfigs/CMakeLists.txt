# The config generator (task 5) on the real C48 manifest: the generated set under build/gen/C48 is what task 6
# runs; here it proves the generator works on the mockdata fixture's manifest and that CECE's schema accepts
# the emitted YAML. `uv run --no-sync` uses the image's /opt/venv; the working directory is the repository
# root, so the settings' relative paths (the submodule mapping, the species file, the validator) resolve.

find_program(UV_EXECUTABLE uv REQUIRED)

set(GEN_DIR ${PROJECT_BINARY_DIR}/gen/C48)
add_test(
  NAME gen_configs_c48
  COMMAND
    ${UV_EXECUTABLE} run --no-sync python -m tools.gen_configs
    --manifest data/C48/manifest.yaml --out ${GEN_DIR} --pets 6
  WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}
)
set_tests_properties(
  gen_configs_c48
  PROPERTIES
    FIXTURES_REQUIRED mockdata
    FIXTURES_SETUP genconfigs
    LABELS ci
    TIMEOUT 120
)

# CECE's own validator on the emitted configuration (the generator ran it too; this is the independent check).
add_test(
  NAME gen_configs_cece_valid
  COMMAND
    ${UV_EXECUTABLE} run --no-sync python ${PROJECT_SOURCE_DIR}/extern/CECE/scripts/validate_cece_config.py
    ${GEN_DIR}/cece.yaml
  WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}
)
set_tests_properties(
  gen_configs_cece_valid
  PROPERTIES FIXTURES_REQUIRED genconfigs LABELS ci TIMEOUT 120
)
