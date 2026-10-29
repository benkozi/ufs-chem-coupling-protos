# Prototype cece_met_catchem: CECE, MET and CATChem under one driver on the configuration tools/gen_configs.py
# writes from the manifest (task 6). The executable is protos/met_catchem's esmApp; the configs are the
# generated sets under build/gen/<res> (fixtures genconfigs, genconfigs_c96, genconfigs_np12 in
# tests/genconfigs); this directory registers the runs and their checks. See runbook.md section 12.

find_program(UV_EXECUTABLE uv REQUIRED)

set(_log_check ${PROJECT_SOURCE_DIR}/tests/log_check.cmake)

# A prototype run: <ranks> PETs on the generated <res> set in its own run directory; <steps> is the pass
# criterion printed by esmApp.
function(cece_met_catchem_run name res ranks steps fixtures label timeout)
  set(_run_dir ${CMAKE_CURRENT_BINARY_DIR}/run_${res}_np${ranks})
  file(MAKE_DIRECTORY ${_run_dir})
  add_test(
    NAME ${name}
    COMMAND
      ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} ${ranks} ${MPIEXEC_PREFLAGS}
      $<TARGET_FILE:esmApp> ${PROJECT_BINARY_DIR}/gen/${res}/esm.configure
    WORKING_DIRECTORY ${_run_dir}
  )
  set_tests_properties(
    ${name}
    PROPERTIES
      FIXTURES_REQUIRED "${fixtures}"
      LABELS ${label}
      PASS_REGULAR_EXPRESSION "cece_met_catchem: completed ${steps} steps"
      TIMEOUT ${timeout}
  )
endfunction()

cece_met_catchem_run(proto_cece_met_catchem C48 6 120 "mockdata;genconfigs" ci 300)
# CECE takes its Kokkos thread count from the PEs ESMF gives each PET, not from the environment: the run
# is made with OMP_NUM_THREADS unset, which used to give every PET all the node's cores (575 s instead of
# about a minute); the timeout catches that regression and log_cece_met_catchem the cap's line.
set_tests_properties(
  proto_cece_met_catchem
  PROPERTIES
    FIXTURES_SETUP cece_met_catchem_run
    ENVIRONMENT_MODIFICATION "OMP_NUM_THREADS=unset:"
)
cece_met_catchem_run(proto_cece_met_catchem_c96 C96 6 120 "mockdata_c96;genconfigs_c96" c96 7200)

set(_run_c48 ${CMAKE_CURRENT_BINARY_DIR}/run_C48_np6)

# The PET-log evidence of the C48 run (tests/log_check.cmake): every emission import connected and bound,
# every category import-fed, every CECE export nz deep and reference-shared into CATChem and MET; and (task 7)
# CATChem on processes/extemis/source: nuopc_import opening no emission file: no `CATChem: emission open` and
# no aqmio read-mode open (DEBUG lines), with a DEBUG line required so a narrowed log cannot pass vacuously.
file(
  WRITE ${CMAKE_CURRENT_BINARY_DIR}/log_cece_met_catchem.spec
  [=[
require CATChem: RealizeAccepted removed 0 unconnected optional import\(s\)
require CATChem: category anthro: 7 field\(s\) read from NUOPC imports
require CATChem: category fire: 4 field\(s\) read from NUOPC imports
require CATChem: category gmi: 3 field\(s\) read from NUOPC imports
require CATChem: category dms: 1 field\(s\) read from NUOPC imports
require CATChem: category dust: 4 field\(s\) read from NUOPC imports
require CATChem: category fengsha: 1 field\(s\) read from NUOPC imports
require CATChem: 20 emission import\(s\) bound to emission fields
require MET: 43 of 43 exports are reference-shared
count 20 Realized export field "[a-z]+_[A-Za-z0-9_]+" \(48x48x127\)
count 20 CATChem: emission import extemis_[a-z]+_[A-Za-z0-9_]+ ShareStatusField=shared
forbid ShareStatusField=not shared
forbid Removed unconnected export field
forbid owns an open corner ring
require catchem_emis_init: extemis source=nuopc_import
require catchem_emis_update: Emission data updated
forbid CATChem: emission open category=
forbid AQMIO: open iomode=read path=
require MET: hosted import extemis_anthro_SO2 ShareStatusField=shared
require \[Cap\] Kokkos OpenMP threads from the ESMF VM: 1 PE\(s\) on this PET
below 1200000 cece_met_catchem: memory per PET max virt [0-9]+ KB phys ([0-9]+) KB \(at end\)
]=]
)
add_test(
  NAME log_cece_met_catchem
  COMMAND ${CMAKE_COMMAND} -DRUN_DIR=${_run_c48} -DSPEC=${CMAKE_CURRENT_BINARY_DIR}/log_cece_met_catchem.spec -P ${_log_check}
)
set_tests_properties(
  log_cece_met_catchem
  PROPERTIES FIXTURES_REQUIRED cece_met_catchem_run LABELS ci TIMEOUT 120
)

# The memory ceiling's failure case: log_check's `below` on a log line over the bound (task 6's 2.35 GB per
# PET, before the generator sized CECE's AMIO staging pools) must fail.
set(_over_dir ${CMAKE_CURRENT_BINARY_DIR}/log_check_below_fails)
file(
  WRITE ${_over_dir}/PET0.ESMF_LogFile
  "20261028 220333.180 INFO PET0 cece_met_catchem: memory per PET max virt 3025208 KB phys 2348368 KB (at end)\n"
)
file(
  WRITE ${_over_dir}/below.spec
  "below 1200000 cece_met_catchem: memory per PET max virt [0-9]+ KB phys ([0-9]+) KB \\(at end\\)\n"
)
add_test(
  NAME log_check_below_fails
  COMMAND ${CMAKE_COMMAND} -DRUN_DIR=${_over_dir} -DSPEC=${_over_dir}/below.spec -P ${_log_check}
)
set_tests_properties(
  log_check_below_fails
  PROPERTIES LABELS ci PASS_REGULAR_EXPRESSION "value 2348368 exceeds 1200000" TIMEOUT 60
)

# The `forbid` failure case: a log with an emission-open line must fail the directive log_cece_met_catchem uses.
set(_forbid_dir ${CMAKE_CURRENT_BINARY_DIR}/log_check_forbid_fails)
file(
  WRITE ${_forbid_dir}/PET0.ESMF_LogFile
  "20261029 120000.000 DEBUG PET0 CATChem: emission open category=anthro reader=tile path=/x/anthro.nc\n"
)
file(WRITE ${_forbid_dir}/forbid.spec "forbid CATChem: emission open category=\n")
add_test(
  NAME log_check_forbid_fails
  COMMAND ${CMAKE_COMMAND} -DRUN_DIR=${_forbid_dir} -DSPEC=${_forbid_dir}/forbid.spec -P ${_log_check}
)
set_tests_properties(
  log_check_forbid_fails
  PROPERTIES LABELS ci PASS_REGULAR_EXPRESSION "log_check: forbidden line found" TIMEOUT 60
)

# Task 7: one emission import left unconnected (CECE does not provide extemis_anthro_NO; gen_configs
# --cece-omit) under source: nuopc_import. CATChem's InitializeP2 refuses it on every PET, so esmApp ends
# through its collective finalize with the Initialize failure on stdout; the named error is in the PET logs.
set(_run_unconnected ${CMAKE_CURRENT_BINARY_DIR}/run_C48_np6_unconnected)
file(MAKE_DIRECTORY ${_run_unconnected})
add_test(
  NAME proto_cece_met_catchem_unconnected_fails
  COMMAND
    ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 6 ${MPIEXEC_PREFLAGS}
    $<TARGET_FILE:esmApp> ${PROJECT_BINARY_DIR}/gen/C48_unconnected/esm.configure
  WORKING_DIRECTORY ${_run_unconnected}
)
set_tests_properties(
  proto_cece_met_catchem_unconnected_fails
  PROPERTIES
    FIXTURES_REQUIRED "mockdata;genconfigs_unconnected"
    FIXTURES_SETUP cece_met_catchem_unconnected_run
    LABELS ci
    ENVIRONMENT_MODIFICATION "OMP_NUM_THREADS=unset:"
    PASS_REGULAR_EXPRESSION "cece_met_catchem: Initialize( \\(user\\))? failed"
    FAIL_REGULAR_EXPRESSION "MPI_ABORT|ORTE_ERROR_LOG|completed [0-9]+ steps"
    TIMEOUT 300
)
file(
  WRITE ${CMAKE_CURRENT_BINARY_DIR}/log_cece_met_catchem_unconnected.spec
  [=[
require CATChem: RealizeAccepted removed 1 unconnected optional import\(s\)
require CATChem: extemis source nuopc_import: category anthro has no connected import extemis_anthro_NO \(no file fallback\)
forbid CATChem: emission open category=
forbid AQMIO: open iomode=read path=
]=]
)
add_test(
  NAME log_cece_met_catchem_unconnected
  COMMAND
    ${CMAKE_COMMAND} -DRUN_DIR=${_run_unconnected}
    -DSPEC=${CMAKE_CURRENT_BINARY_DIR}/log_cece_met_catchem_unconnected.spec -P ${_log_check}
)
set_tests_properties(
  log_cece_met_catchem_unconnected
  PROPERTIES FIXTURES_REQUIRED cece_met_catchem_unconnected_run LABELS ci TIMEOUT 120
)

# Emissions accumulate in the host tracer field: MET's hourly area-weighted so2 sum grows from zero.
file(
  WRITE ${CMAKE_CURRENT_BINARY_DIR}/growth_cece_met_catchem.spec
  [=[
grow MET: tracer so2 area-weighted sum ([-+0-9.E]+) at
]=]
)
add_test(
  NAME growth_cece_met_catchem
  COMMAND ${CMAKE_COMMAND} -DRUN_DIR=${_run_c48} -DSPEC=${CMAKE_CURRENT_BINARY_DIR}/growth_cece_met_catchem.spec -P ${_log_check}
)
set_tests_properties(
  growth_cece_met_catchem
  PROPERTIES FIXTURES_REQUIRED cece_met_catchem_run LABELS ci TIMEOUT 120
)

# Conservation: level 1 of the SO2 export as CATChem binds it (MET reference-shares the same allocation and
# writes met_import_totals.yaml) against the inventory totals at the hourly stamps; levels 2..nz must be zero.
set(_checker ${UV_EXECUTABLE} run --no-sync python -m tools.check_conservation
    --expected ${PROJECT_SOURCE_DIR}/data/C48/inventories/totals.yaml
    --run-dir ${_run_c48}
    --category anthro --variable SO2 --level 1 --zero-above)
# Prior: task 4's measured AXIS consd error for anthro/NO (1.529e-6, registered 2e-6); re-measured here.
add_test(NAME conservation_cece_met_catchem COMMAND ${_checker} --rtol 2e-6 WORKING_DIRECTORY ${PROJECT_SOURCE_DIR})
set_tests_properties(
  conservation_cece_met_catchem
  PROPERTIES FIXTURES_REQUIRED "mockdata;cece_met_catchem_run" LABELS ci TIMEOUT 120
)
add_test(
  NAME conservation_cece_met_catchem_tight_tolerance_fails
  COMMAND ${_checker} --rtol 1e-15
  WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}
)
set_tests_properties(
  conservation_cece_met_catchem_tight_tolerance_fails
  PROPERTIES
    FIXTURES_REQUIRED "mockdata;cece_met_catchem_run"
    LABELS ci
    PASS_REGULAR_EXPRESSION "exceeds rtol"
    TIMEOUT 120
)

# 12 PETs (2 DEs per tile) need a corner halo CECE's host-block path does not have (deferred to task 9). The
# driver refuses the layout on every PET before CECE starts; inside CECE the PETs with an open corner ring
# would fail while the others wait in a collective, a hang until the TIMEOUT.
file(MAKE_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}/run_C48_np12)
add_test(
  NAME proto_cece_met_catchem_np12_fails
  COMMAND
    ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 12 ${MPIEXEC_PREFLAGS}
    $<TARGET_FILE:esmApp> ${PROJECT_BINARY_DIR}/gen/C48_np12/esm.configure
  WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}/run_C48_np12
)
set_tests_properties(
  proto_cece_met_catchem_np12_fails
  PROPERTIES
    FIXTURES_REQUIRED "mockdata;genconfigs_np12"
    LABELS ci
    PASS_REGULAR_EXPRESSION "CECE needs one DE per tile"
    FAIL_REGULAR_EXPRESSION "MPI_ABORT|ORTE_ERROR_LOG"
    TIMEOUT 300
)

# Profiling (label `profile`, never `ci`; runbook section 13): the C48 run with ESMF's profiler, whose
# summary must hold the cap's trace regions, and, when Kokkos Tools is present (the image builds it under
# --build-arg KOKKOS_TOOLS=on, `tools/ci.sh test profile` passes it), the space-time-stack report of the
# core's Kokkos regions, which the run's stdout carries (tee'd to kokkos_stdout.txt). Without the tool the
# Kokkos check is reported as skipped and the ESMF layer still splits cap from core.
find_library(
  KOKKOS_TOOLS_SPACE_TIME_STACK
  kp_space_time_stack
  HINTS /opt/kokkos-tools/lib $ENV{KOKKOS_TOOLS_ROOT}/lib
)
set(_run_profile ${CMAKE_CURRENT_BINARY_DIR}/run_C48_np6_profile)
file(MAKE_DIRECTORY ${_run_profile})
set(_profile_env "ESMF_RUNTIME_PROFILE=ON;ESMF_RUNTIME_PROFILE_OUTPUT=SUMMARY")
if(KOKKOS_TOOLS_SPACE_TIME_STACK)
  list(APPEND _profile_env "KOKKOS_TOOLS_LIBS=${KOKKOS_TOOLS_SPACE_TIME_STACK}")
endif()
add_test(
  NAME proto_cece_met_catchem_profile
  COMMAND
    bash -c "set -o pipefail; \"$0\" \"$@\" 2>&1 | tee kokkos_stdout.txt" ${MPIEXEC_EXECUTABLE}
    ${MPIEXEC_NUMPROC_FLAG} 6 ${MPIEXEC_PREFLAGS} $<TARGET_FILE:esmApp>
    ${PROJECT_BINARY_DIR}/gen/C48/esm.configure
  WORKING_DIRECTORY ${_run_profile}
)
set_tests_properties(
  proto_cece_met_catchem_profile
  PROPERTIES
    FIXTURES_REQUIRED "mockdata;genconfigs"
    FIXTURES_SETUP cece_met_catchem_profile_run
    LABELS profile
    ENVIRONMENT "${_profile_env}"
    PASS_REGULAR_EXPRESSION "cece_met_catchem: completed 120 steps"
    TIMEOUT 600
)

file(
  WRITE ${CMAKE_CURRENT_BINARY_DIR}/profile_esmf_cece_met_catchem.spec
  [=[
require \[CECE\] RunPhase1
require CECE: import copy
require CECE: cece_sim_step
require CECE: timestamp
require CECE: create sim
require CECE: bind exports
]=]
)
add_test(
  NAME profile_esmf_cece_met_catchem
  COMMAND
    ${CMAKE_COMMAND} -DRUN_DIR=${_run_profile} -DFILES=ESMF_Profile.summary
    -DSPEC=${CMAKE_CURRENT_BINARY_DIR}/profile_esmf_cece_met_catchem.spec -P ${_log_check}
)
set_tests_properties(
  profile_esmf_cece_met_catchem
  PROPERTIES FIXTURES_REQUIRED cece_met_catchem_profile_run LABELS profile TIMEOUT 60
)

if(KOKKOS_TOOLS_SPACE_TIME_STACK)
  file(
    WRITE ${CMAKE_CURRENT_BINARY_DIR}/profile_kokkos_cece_met_catchem.spec
    [=[
require CECE::Step
require CECE::AdvanceTime
require CECE::Regrid
require CECE::WriteBandToImport
require CECE::Run
require CECE::Stacking
require CECE::ExportCopy
]=]
  )
  add_test(
    NAME profile_kokkos_cece_met_catchem
    COMMAND
      ${CMAKE_COMMAND} -DRUN_DIR=${_run_profile} -DFILES=kokkos_stdout.txt
      -DSPEC=${CMAKE_CURRENT_BINARY_DIR}/profile_kokkos_cece_met_catchem.spec -P ${_log_check}
  )
else()
  add_test(
    NAME profile_kokkos_cece_met_catchem
    COMMAND ${CMAKE_COMMAND} -E echo "Kokkos Tools not found (kp_space_time_stack): Kokkos readout skipped"
  )
  set_tests_properties(profile_kokkos_cece_met_catchem PROPERTIES SKIP_REGULAR_EXPRESSION "readout skipped")
endif()
set_tests_properties(
  profile_kokkos_cece_met_catchem
  PROPERTIES FIXTURES_REQUIRED cece_met_catchem_profile_run LABELS profile TIMEOUT 60
)
