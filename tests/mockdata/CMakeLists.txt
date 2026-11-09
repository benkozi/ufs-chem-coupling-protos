# The mock data step as CTest fixtures. `make_mock_data` (label ci) generates the C48 set the prototypes need
# (FIXTURES_SETUP mockdata; a prototype test declares FIXTURES_REQUIRED mockdata), so `ctest -R proto_`
# builds its own inputs. The C96 variant is labelled c96 and never runs under `ctest -L ci`.
# `uv run --no-sync` uses the image's /opt/venv (UV_PROJECT_ENVIRONMENT) as baked from uv.lock; the working
# directory is the repository root, so data/ lands on the bind mount (git-ignored, outside the build context).

find_program(UV_EXECUTABLE uv REQUIRED)

add_test(
  NAME make_mock_data
  COMMAND ${UV_EXECUTABLE} run --no-sync python -m tools.make_mock_data --resolution C48 --out data/C48
  WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}
)
set_tests_properties(
  make_mock_data
  PROPERTIES
    FIXTURES_SETUP mockdata
    LABELS ci
    TIMEOUT 900
)

add_test(
  NAME make_mock_data_c96
  COMMAND ${UV_EXECUTABLE} run --no-sync python -m tools.make_mock_data --resolution C96 --out data/C96
  WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}
)
set_tests_properties(
  make_mock_data_c96
  PROPERTIES
    FIXTURES_SETUP mockdata_c96
    LABELS c96
    TIMEOUT 1800
)

# The realistic profile (issue #17): CATChem's production inventories mirrored from the ExtData catalog, coarsened
# ten times for CI (0.1° -> 1°) and five times for the C96 set (0.5°), under data/<res>_realistic/ExtData: its own
# directory, because the analytic fixture's manifest.yaml and inventories/totals.yaml under data/<res> are what the
# generator and conservation tests read (the first cloud run of this fixture overwrote them). Its parameter set is written by tools/realistic_defaults.py
# from the Ursa scrape, so the tests are registered only once that file is checked in.
if(EXISTS ${PROJECT_SOURCE_DIR}/tools/defaults/manifest_realistic.yaml)
  add_test(
    NAME make_mock_data_realistic
    COMMAND ${UV_EXECUTABLE} run --no-sync python -m tools.make_mock_data --resolution C48 --out data/C48_realistic
            --profile realistic --grid-factor 10
    WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}
  )
  set_tests_properties(
    make_mock_data_realistic
    PROPERTIES
      FIXTURES_SETUP mockdata_realistic
      LABELS ci
      TIMEOUT 900
  )

  add_test(
    NAME make_mock_data_realistic_c96
    COMMAND ${UV_EXECUTABLE} run --no-sync python -m tools.make_mock_data --resolution C96 --out data/C96_realistic
            --profile realistic --grid-factor 5
    WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}
  )
  set_tests_properties(
    make_mock_data_realistic_c96
    PROPERTIES
      FIXTURES_SETUP mockdata_realistic_c96
      LABELS c96
      TIMEOUT 3600
  )
endif()
