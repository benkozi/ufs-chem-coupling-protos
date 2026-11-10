# Prototype cece_met: CECE on the cubed-sphere tile grid with MET as the consumer (task 4). The executable is
# protos/met_catchem's esmApp (EARTH_component_list: ATM CECE); this directory renders the configs and
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

# Issue 16: CECE takes its grid from MET's realized tracer field (CECE_SetHostGeometry), not from a grid the
# driver attaches. The cap's provenance lines are the evidence, on the PET logs.
set(_log_check ${PROJECT_SOURCE_DIR}/tests/log_check.cmake)
file(
  WRITE ${CMAKE_CURRENT_BINARY_DIR}/log_cece_met.spec
  [=[
require \[Cap\] grid source: host field ATM/inst_tracer_mass_frac \(tileCount=6, local DE [0-5] of tile [1-6]\)
require \[Cap\] Host grid block: .* \(48x48\), corners 49x49
forbid \[Cap\] Parent grid:
forbid \[Cap\] No parent grid or mesh
require MET: run phase phase1 \(exports filled\) first entered at 2021-03-22T06:00:00
require MET: run phase phase2 \(tracers taken, hosted imports totalled\) first entered at 2021-03-22T06:00:00
forbid MET: no default run phase
require MET: hosted import extemis_anthro_NO ShareStatusField=shared
]=]
)
add_test(
  NAME log_cece_met
  COMMAND
    ${CMAKE_COMMAND} -DRUN_DIR=${CMAKE_CURRENT_BINARY_DIR}/run_C48_np6
    -DSPEC=${CMAKE_CURRENT_BINARY_DIR}/log_cece_met.spec -P ${_log_check}
)
set_tests_properties(log_cece_met PROPERTIES FIXTURES_REQUIRED cece_met_run LABELS ci TIMEOUT 120)

# The component-order rule's failure case: the driver runs each initialize phase over its children in
# EARTH_component_list order, so with CECE listed before ATM (the label of MET, the analytic provider) the cap finds its tracer field unrealized and
# says so. esmApp prints only the failing phase; the named error is checked on the PET logs.
file(READ ${CMAKE_CURRENT_BINARY_DIR}/esm.C48.configure _cece_met_config)
string(REPLACE "EARTH_component_list: ATM CECE" "EARTH_component_list: CECE ATM" _bad_order_config "${_cece_met_config}")
if(_bad_order_config STREQUAL _cece_met_config)
  message(FATAL_ERROR "cece_met: esm.configure.in no longer has 'EARTH_component_list: ATM CECE'; update the bad-order case")
endif()
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/esm.C48.bad_order.configure "${_bad_order_config}")
set(_run_bad_order ${CMAKE_CURRENT_BINARY_DIR}/run_C48_np6_bad_order)
file(MAKE_DIRECTORY ${_run_bad_order})
add_test(
  NAME proto_cece_met_bad_order_fails
  COMMAND
    ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 6 ${MPIEXEC_PREFLAGS}
    $<TARGET_FILE:esmApp> ${CMAKE_CURRENT_BINARY_DIR}/esm.C48.bad_order.configure
  WORKING_DIRECTORY ${_run_bad_order}
)
set_tests_properties(
  proto_cece_met_bad_order_fails
  PROPERTIES
    FIXTURES_REQUIRED mockdata
    FIXTURES_SETUP cece_met_bad_order_run
    LABELS ci
    PASS_REGULAR_EXPRESSION "cece_met: Initialize( \\(user\\))? failed"
    FAIL_REGULAR_EXPRESSION "MPI_ABORT|ORTE_ERROR_LOG|completed [0-9]+ steps"
    TIMEOUT 300
)
file(
  WRITE ${CMAKE_CURRENT_BINARY_DIR}/log_cece_met_bad_order.spec
  [=[
require \[Cap\] host geometry field inst_tracer_mass_frac of ATM is not realized; the host must precede CECE
]=]
)
add_test(
  NAME log_cece_met_bad_order
  COMMAND
    ${CMAKE_COMMAND} -DRUN_DIR=${_run_bad_order} -DSPEC=${CMAKE_CURRENT_BINARY_DIR}/log_cece_met_bad_order.spec
    -P ${_log_check}
)
set_tests_properties(log_cece_met_bad_order PROPERTIES FIXTURES_REQUIRED cece_met_bad_order_run LABELS ci TIMEOUT 120)

# The phase-order rule's failure case: a runSeq:: that runs `ATM phase2` before `ATM phase1` stops in the first
# step with MET's own message (the generator refuses such a sequence; a hand-written file gets this far).
string(REPLACE "    ATM phase1\n" "    ATM phaseX\n" _bad_phase_config "${_cece_met_config}")
string(REPLACE "    ATM phase2\n" "    ATM phase1\n" _bad_phase_config "${_bad_phase_config}")
string(REPLACE "    ATM phaseX\n" "    ATM phase2\n" _bad_phase_config "${_bad_phase_config}")
if(_bad_phase_config STREQUAL _cece_met_config)
  message(FATAL_ERROR "cece_met: esm.configure.in no longer has the `ATM phase1` and `ATM phase2` lines; update the bad-phase-order case")
endif()
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/esm.C48.bad_phase_order.configure "${_bad_phase_config}")
set(_run_bad_phase ${CMAKE_CURRENT_BINARY_DIR}/run_C48_np6_bad_phase_order)
file(MAKE_DIRECTORY ${_run_bad_phase})
add_test(
  NAME proto_cece_met_bad_phase_order_fails
  COMMAND
    ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 6 ${MPIEXEC_PREFLAGS}
    $<TARGET_FILE:esmApp> ${CMAKE_CURRENT_BINARY_DIR}/esm.C48.bad_phase_order.configure
  WORKING_DIRECTORY ${_run_bad_phase}
)
set_tests_properties(
  proto_cece_met_bad_phase_order_fails
  PROPERTIES
    FIXTURES_REQUIRED mockdata
    FIXTURES_SETUP cece_met_bad_phase_order_run
    LABELS ci
    PASS_REGULAR_EXPRESSION "cece_met: Run( \\(user\\))? failed"
    FAIL_REGULAR_EXPRESSION "MPI_ABORT|ORTE_ERROR_LOG|completed [0-9]+ steps"
    TIMEOUT 300
)
file(
  WRITE ${CMAKE_CURRENT_BINARY_DIR}/log_cece_met_bad_phase_order.spec
  [=[
require MET: run phase phase2 entered before phase1 in this step
forbid MET: run phase phase2 \(tracers taken
]=]
)
add_test(
  NAME log_cece_met_bad_phase_order
  COMMAND
    ${CMAKE_COMMAND} -DRUN_DIR=${_run_bad_phase} -DSPEC=${CMAKE_CURRENT_BINARY_DIR}/log_cece_met_bad_phase_order.spec
    -P ${_log_check}
)
set_tests_properties(
  log_cece_met_bad_phase_order
  PROPERTIES FIXTURES_REQUIRED cece_met_bad_phase_order_run LABELS ci TIMEOUT 120
)

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
