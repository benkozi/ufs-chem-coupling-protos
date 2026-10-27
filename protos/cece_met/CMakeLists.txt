# Prototype cece_met: CECE on the cubed-sphere tile grid with MET as the consumer (task 4). The executable is
# protos/met_catchem's esmApp (EARTH_component_list: CECE MET); this directory renders the configs and
# registers the tests. See runbook.md section 10.

find_program(UV_EXECUTABLE uv REQUIRED)

# The clock of the CECE config from tools/defaults/manifest.yaml, like STEP_SECONDS in protos/met_catchem.
file(STRINGS ${PROJECT_SOURCE_DIR}/tools/defaults/manifest.yaml _start_line REGEX "^  start:")
string(REGEX REPLACE "^  start: *'?([0-9T:-]+)Z?'?.*$" "\\1" CECE_START "${_start_line}")
file(STRINGS ${PROJECT_SOURCE_DIR}/tools/defaults/manifest.yaml _hours_line REGEX "^  duration_hours:")
string(REGEX REPLACE "^  duration_hours: *([0-9]+).*$" "\\1" DURATION_HOURS "${_hours_line}")
if(NOT CECE_START MATCHES "^[0-9]+-[0-9]+-[0-9]+T[0-9]+:[0-9]+:[0-9]+$" OR NOT DURATION_HOURS MATCHES "^[0-9]+$")
  message(FATAL_ERROR "cannot read clock.start/duration_hours from tools/defaults/manifest.yaml (got '${_start_line}', '${_hours_line}')")
endif()
# end_time = start + duration, through the epoch (GNU date; the configure step runs in the Linux image).
# "<ISO>Z" is parsed as UTC; the bare "<date>T<time> + N hours" form misreads the offset.
execute_process(
  COMMAND date -u -d "${CECE_START}Z" "+%s"
  OUTPUT_VARIABLE _start_epoch
  OUTPUT_STRIP_TRAILING_WHITESPACE
  RESULT_VARIABLE _date_rc
)
if(NOT _date_rc EQUAL 0 OR NOT _start_epoch MATCHES "^[0-9]+$")
  message(FATAL_ERROR "cannot parse the manifest start ${CECE_START} with GNU date: '${_start_epoch}'")
endif()
math(EXPR _end_epoch "${_start_epoch} + ${DURATION_HOURS} * 3600")
execute_process(
  COMMAND date -u -d "@${_end_epoch}" "+%Y-%m-%dT%H:%M:%S"
  OUTPUT_VARIABLE CECE_END
  OUTPUT_STRIP_TRAILING_WHITESPACE
  RESULT_VARIABLE _date_rc
)
if(NOT _date_rc EQUAL 0 OR NOT CECE_END MATCHES "^[0-9]+-[0-9]+-[0-9]+T[0-9]+:[0-9]+:[0-9]+$")
  message(FATAL_ERROR "cannot compute the CECE end_time with GNU date (start ${CECE_START}, ${DURATION_HOURS} h): '${CECE_END}'")
endif()
message(STATUS "cece_met: CECE clock ${CECE_START} .. ${CECE_END}, step ${STEP_SECONDS} s")

set(PROTOS_ROOT ${PROJECT_SOURCE_DIR})
foreach(RES IN ITEMS C48 C96)
  set(CECE_CONFIG ${CMAKE_CURRENT_BINARY_DIR}/cece_anthro_no.${RES}.yaml)
  configure_file(${PROJECT_SOURCE_DIR}/configs/prescribed/cece_met/cece_anthro_no.yaml.in ${CECE_CONFIG} @ONLY)
  configure_file(esm.configure.in ${CMAKE_CURRENT_BINARY_DIR}/esm.${RES}.configure @ONLY)
endforeach()

# The rendered CECE YAML against CECE's own schema (scripts/validate_cece_config.py, standard library only).
add_test(
  NAME cece_config_valid
  COMMAND
    ${UV_EXECUTABLE} run --no-sync python ${PROJECT_SOURCE_DIR}/extern/CECE/scripts/validate_cece_config.py
    ${CMAKE_CURRENT_BINARY_DIR}/cece_anthro_no.C48.yaml
  WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}
)
set_tests_properties(cece_config_valid PROPERTIES LABELS ci TIMEOUT 120)

# A prototype run: <ranks> PETs on the <res> config in its own run directory; <steps> is the pass criterion.
function(cece_met_run name res ranks steps fixture label timeout)
  set(_run_dir ${CMAKE_CURRENT_BINARY_DIR}/run_${res}_np${ranks})
  file(MAKE_DIRECTORY ${_run_dir})
  add_test(
    NAME ${name}
    COMMAND
      ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} ${ranks} ${MPIEXEC_PREFLAGS}
      $<TARGET_FILE:esmApp> ${CMAKE_CURRENT_BINARY_DIR}/esm.${res}.configure
    WORKING_DIRECTORY ${_run_dir}
  )
  set_tests_properties(
    ${name}
    PROPERTIES
      FIXTURES_REQUIRED ${fixture}
      LABELS ${label}
      PASS_REGULAR_EXPRESSION "cece_met: completed ${steps} steps"
      TIMEOUT ${timeout}
  )
endfunction()

cece_met_run(proto_cece_met C48 6 120 mockdata ci 1800)
set_tests_properties(proto_cece_met PROPERTIES FIXTURES_SETUP cece_met_run)
cece_met_run(proto_cece_met_c96 C96 6 120 mockdata_c96 c96 7200)

# Conservation: MET's totals against the task-2 inventory totals at the hourly record times (where CECE's
# linear interpolation equals the record). The tolerance is the measured AXIS consd error, rounded up
# (runbook section 10); the tight-tolerance registration is the rule's failure case.
set(_checker ${UV_EXECUTABLE} run --no-sync python -m tools.check_conservation
    --expected ${PROJECT_SOURCE_DIR}/data/C48/inventories/totals.yaml
    --actual ${CMAKE_CURRENT_BINARY_DIR}/run_C48_np6/met_import_totals.yaml
    --category anthro --variable NO)
# Measured 2026-10-27 on the cloud builder: max relative error 1.529e-6 (02:00 next day); rounded up.
add_test(NAME conservation_cece COMMAND ${_checker} --rtol 2e-6 WORKING_DIRECTORY ${PROJECT_SOURCE_DIR})
set_tests_properties(
  conservation_cece
  PROPERTIES FIXTURES_REQUIRED "mockdata;cece_met_run" LABELS ci TIMEOUT 120
)
add_test(NAME conservation_cece_tight_tolerance_fails COMMAND ${_checker} --rtol 1e-15 WORKING_DIRECTORY ${PROJECT_SOURCE_DIR})
set_tests_properties(
  conservation_cece_tight_tolerance_fails
  PROPERTIES
    FIXTURES_REQUIRED "mockdata;cece_met_run"
    LABELS ci
    PASS_REGULAR_EXPRESSION "exceeds rtol"
    TIMEOUT 120
)
