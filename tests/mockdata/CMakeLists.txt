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
