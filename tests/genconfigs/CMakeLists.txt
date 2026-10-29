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

# Task 6: the C96 set for proto_cece_met_catchem_c96 (label c96, after the C96 mock data).
add_test(
  NAME gen_configs_c96
  COMMAND
    ${UV_EXECUTABLE} run --no-sync python -m tools.gen_configs
    --manifest data/C96/manifest.yaml --out ${PROJECT_BINARY_DIR}/gen/C96 --pets 6
  WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}
)
set_tests_properties(
  gen_configs_c96
  PROPERTIES
    FIXTURES_REQUIRED mockdata_c96
    FIXTURES_SETUP genconfigs_c96
    LABELS c96
    TIMEOUT 120
)

# Task 6: a 12-PET C48 set (2 DEs per tile) for the corner-ring failure case of proto_cece_met_catchem.
add_test(
  NAME gen_configs_c48_np12
  COMMAND
    ${UV_EXECUTABLE} run --no-sync python -m tools.gen_configs
    --manifest data/C48/manifest.yaml --out ${PROJECT_BINARY_DIR}/gen/C48_np12 --pets 12
  WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}
)
set_tests_properties(
  gen_configs_c48_np12
  PROPERTIES
    FIXTURES_REQUIRED mockdata
    FIXTURES_SETUP genconfigs_np12
    LABELS ci
    TIMEOUT 120
)
