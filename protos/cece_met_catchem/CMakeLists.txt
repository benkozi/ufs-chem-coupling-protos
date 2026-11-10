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
require \[Cap\] grid source: host field ATM/inst_tracer_mass_frac \(tileCount=6, local DE [0-5] of tile [1-6]\)
require \[Cap\] Host grid block: .* \(48x48\), corners 49x49
forbid \[Cap\] Parent grid:
forbid \[Cap\] No parent grid or mesh
require MET: run phase phase1 \(exports filled\) first entered at 2021-03-22T06:00:00
require MET: run phase phase2 \(tracers taken, hosted imports totalled\) first entered at 2021-03-22T06:00:00
forbid MET: no default run phase
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

# Issue #18: the same three components on the realistic set (CATChem's 19 production categories at their real
# paths, grids, time axes and units, coarsened ten times; 33 emission imports, two hosted by MET). The run
# directory and config path follow from the res name C48_realistic. Registered only with the realistic defaults.
# CECE's per-step stream lines ([DRIVER] Reading/Interpolating time slice ...) go to stdout, not to the PET
# logs, so the run tees its stdout into cece_stdout.txt in the run directory (as the profile run does) and the
# timing check reads that file.
if(EXISTS ${PROJECT_SOURCE_DIR}/tools/defaults/manifest_realistic.yaml)
  set(_run_realistic ${CMAKE_CURRENT_BINARY_DIR}/run_C48_realistic_np6)
  file(MAKE_DIRECTORY ${_run_realistic})
  add_test(
    NAME proto_cece_met_catchem_realistic
    COMMAND
      bash -c "set -o pipefail; \"$0\" \"$@\" 2>&1 | tee cece_stdout.txt" ${MPIEXEC_EXECUTABLE}
      ${MPIEXEC_NUMPROC_FLAG} 6 ${MPIEXEC_PREFLAGS} $<TARGET_FILE:esmApp>
      ${PROJECT_BINARY_DIR}/gen/C48_realistic/esm.configure
    WORKING_DIRECTORY ${_run_realistic}
  )
  set_tests_properties(
    proto_cece_met_catchem_realistic
    PROPERTIES
      FIXTURES_REQUIRED "mockdata_realistic;genconfigs_realistic"
      FIXTURES_SETUP cece_met_catchem_realistic_run
      LABELS ci
      ENVIRONMENT_MODIFICATION "OMP_NUM_THREADS=unset:"
      PASS_REGULAR_EXPRESSION "cece_met_catchem: completed 120 steps"
      TIMEOUT 600
  )

  # The PET-log evidence: every one of the 33 imports connected, bound and reference-shared, every one of the 18
  # gridded categories import-fed, both hosted imports shared, no emission file opened, every unit and vertical
  # distribution recognised by CATChem, no CECE fatal, and the memory ceiling (33 nz-deep exports, 16 data files).
  file(
    WRITE ${CMAKE_CURRENT_BINARY_DIR}/log_cece_met_catchem_realistic.spec
    [=[
require CATChem: RealizeAccepted removed 0 unconnected optional import\(s\)
require CATChem: category anthro1: 3 field\(s\) read from NUOPC imports
require CATChem: category anthro2: 3 field\(s\) read from NUOPC imports
require CATChem: category ship: 4 field\(s\) read from NUOPC imports
require CATChem: category fire: 1 field\(s\) read from NUOPC imports
require CATChem: category bio: 4 field\(s\) read from NUOPC imports
require CATChem: category so2_aviation_lto: 1 field\(s\) read from NUOPC imports
require CATChem: category oc_aviation_lto: 1 field\(s\) read from NUOPC imports
require CATChem: category bc_aviation_lto: 1 field\(s\) read from NUOPC imports
require CATChem: category so2_aviation_cds: 1 field\(s\) read from NUOPC imports
require CATChem: category oc_aviation_cds: 1 field\(s\) read from NUOPC imports
require CATChem: category bc_aviation_cds: 1 field\(s\) read from NUOPC imports
require CATChem: category so2_aviation_crs: 1 field\(s\) read from NUOPC imports
require CATChem: category oc_aviation_crs: 1 field\(s\) read from NUOPC imports
require CATChem: category bc_aviation_crs: 1 field\(s\) read from NUOPC imports
require CATChem: category gmi: 3 field\(s\) read from NUOPC imports
require CATChem: category dms: 1 field\(s\) read from NUOPC imports
require CATChem: category dust: 4 field\(s\) read from NUOPC imports
require CATChem: category fengsha: 1 field\(s\) read from NUOPC imports
require CATChem: 33 emission import\(s\) bound to emission fields
require MET: 43 of 43 exports are reference-shared
count 33 Realized export field "[a-z0-9_]+_[A-Za-z0-9_]+" \(48x48x127\)
count 33 CATChem: emission import extemis_[a-z0-9_]+_[A-Za-z0-9_]+ ShareStatusField=shared
forbid ShareStatusField=not shared
forbid Removed unconnected export field
forbid owns an open corner ring
require catchem_emis_init: extemis source=nuopc_import
require catchem_emis_update: Emission data updated
forbid CATChem: emission open category=
forbid AQMIO: open iomode=read path=
require MET: hosted import extemis_anthro1_SO2 ShareStatusField=shared
require MET: hosted import extemis_dust_sep ShareStatusField=shared
forbid Unrecognized emission field units
forbid Unrecognized vertical_dist option
forbid DRIVER FATAL
require \[Cap\] grid source: host field ATM/inst_tracer_mass_frac \(tileCount=6, local DE [0-5] of tile [1-6]\)
require \[Cap\] Host grid block: .* \(48x48\), corners 49x49
forbid \[Cap\] Parent grid:
forbid \[Cap\] No parent grid or mesh
require MET: run phase phase1 \(exports filled\) first entered at 2021-03-22T06:00:00
require MET: run phase phase2 \(tracers taken, hosted imports totalled\) first entered at 2021-03-22T06:00:00
forbid MET: no default run phase
require \[Cap\] Kokkos OpenMP threads from the ESMF VM: 1 PE\(s\) on this PET
below 1200000 cece_met_catchem: memory per PET max virt [0-9]+ KB phys ([0-9]+) KB \(at end\)
]=]
  )
  add_test(
    NAME log_cece_met_catchem_realistic
    COMMAND
      ${CMAKE_COMMAND} -DRUN_DIR=${_run_realistic}
      -DSPEC=${CMAKE_CURRENT_BINARY_DIR}/log_cece_met_catchem_realistic.spec -P ${_log_check}
  )
  set_tests_properties(
    log_cece_met_catchem_realistic
    PROPERTIES FIXTURES_REQUIRED cece_met_catchem_realistic_run LABELS ci TIMEOUT 120
  )

  # Stream timing, on CECE's per-step slice lines (logged when a stream's bracket changes), following CATChem's
  # time_interpolation per category. Held (none -> tintalgo nearest): the CEDS window is read as record 2 (22
  # March) at 06Z and as record 3 (23 March) from midnight on, never blended, which is what the production run
  # opened; GMI is read as its March record. Blended (linear): CECE's time_label 'auto' centres a day-spaced
  # record on its day (the MAPL/CATChem 12Z knots), so the QFED and MEGAN windows blend records 1 and 2 at 06Z
  # and 2 and 3 from 12:12 on; the 2010 HTAP, 2011 DMS and 2022 FENGSHA climatologies are read by year wrap
  # ("decoded axis", never the arithmetic fallback). The soil file, which now carries a 12-record time axis and
  # a time-bearing sibling as the real one does, serves its timeless variables as one record.
  file(
    WRITE ${CMAKE_CURRENT_BINARY_DIR}/timing_cece_met_catchem_realistic.spec
    [=[
require \[DRIVER\] Reading time slice 2/4 from '.*streams/CEDS\.2019\.emis\.nc' for field 'anthro1_SO2' \(decoded axis, time=2021-03-22T06:00:00
require \[DRIVER\] Reading time slice 3/4 from '.*streams/CEDS\.2019\.emis\.nc' for field 'anthro1_SO2' \(decoded axis, time=2021-03-23T00:00:00
forbid \[DRIVER\] Interpolating time slices .*streams/CEDS\.2019\.emis\.nc'
require \[DRIVER\] Interpolating time slices 1 & 2/4 \(w=0\.75[0-9]*\) from '.*streams/qfed2\.emis_so2\.006\.nc4' for field 'fire_biomass' \(decoded axis, .*time=2021-03-22T06:00:00
require \[DRIVER\] Interpolating time slices 2 & 3/4 .*streams/qfed2\.emis_so2\.006\.nc4' for field 'fire_biomass' \(decoded axis, .*time=2021-03-22T12:12:00
require \[DRIVER\] Interpolating time slices 2 & 3/4 .*streams/MEGAN\.OFFLINE\.BIOVOC\.emis\.nc' for field 'bio_isoprene' \(decoded axis
require \[DRIVER\] Interpolating time slices 3 & 4/12 .*htap-v2\.2\.emis_so2\.aviation_lto.*' for field 'so2_aviation_lto_so2_aviation' \(decoded axis
require \[DRIVER\] Interpolating time slices 3 & 4/12 .*DMSclim_sfcconcentration.*' for field 'dms_conc' \(decoded axis
require \[DRIVER\] Interpolating time slices 3 & 4/12 .*FENGSHA_New_Method_NESDISv1\.1_9km\.nc' for field 'fengsha_PC' \(decoded axis
require \[DRIVER\] Reading time slice 4/14 from '.*A2_ACCMIP_gmic_MERRA.*' for field 'gmi_oh' \(decoded axis
forbid \[DRIVER\] Interpolating time slices .*A2_ACCMIP_gmic_MERRA
require \[DRIVER\] Reading time slice 1/1 from '.*FENGSHA_2022_NESDIS_inputs_10km_v3\.2\.nc' for field 'dust_clayfrac' \(single record
require \[DRIVER\] Reading time slice 1/1 from '.*FENGSHA_2022_NESDIS_inputs_10km_v3\.2\.nc' for field 'dust_sep' \(single record
forbid degraded arithmetic
forbid outside the coverage
forbid DRIVER FATAL
]=]
  )
  add_test(
    NAME timing_cece_met_catchem_realistic
    COMMAND
      ${CMAKE_COMMAND} -DRUN_DIR=${_run_realistic} -DFILES=cece_stdout.txt
      -DSPEC=${CMAKE_CURRENT_BINARY_DIR}/timing_cece_met_catchem_realistic.spec -P ${_log_check}
  )
  set_tests_properties(
    timing_cece_met_catchem_realistic
    PROPERTIES FIXTURES_REQUIRED cece_met_catchem_realistic_run LABELS ci TIMEOUT 120
  )

  file(
    WRITE ${CMAKE_CURRENT_BINARY_DIR}/growth_cece_met_catchem_realistic.spec
    [=[
grow MET: tracer so2 area-weighted sum ([-+0-9.E]+) at
]=]
  )
  add_test(
    NAME growth_cece_met_catchem_realistic
    COMMAND
      ${CMAKE_COMMAND} -DRUN_DIR=${_run_realistic}
      -DSPEC=${CMAKE_CURRENT_BINARY_DIR}/growth_cece_met_catchem_realistic.spec -P ${_log_check}
  )
  set_tests_properties(
    growth_cece_met_catchem_realistic
    PROPERTIES FIXTURES_REQUIRED cece_met_catchem_realistic_run LABELS ci TIMEOUT 120
  )

  # Conservation on the two hosted imports. A daily or static inventory has one record stamp inside the run
  # (CEDS 2021-03-23T00Z, where the held record of 23 March comes into force; the soil file's single record at
  # the clock start), so --min-matches 1; the analytic run keeps the 24-stamp check. Tolerances are the
  # measured errors rounded up (cloud builder): consd from the coarsened 1 deg CEDS grid 1.513e-5; nn of sep
  # from the 1 deg soil grid 2.008e-3 (nearest-neighbour is not conservative: this check proves the timeless
  # read, not the physics). sep is the hosted soil variable because clayfrac and sandfrac carry fill cells
  # over the ocean, as the real file does.
  set(_checker_realistic ${UV_EXECUTABLE} run --no-sync python -m tools.check_conservation
      --expected ${PROJECT_SOURCE_DIR}/data/C48_realistic/inventories/totals.yaml
      --run-dir ${_run_realistic} --level 1 --zero-above --min-matches 1)
  add_test(
    NAME conservation_cece_met_catchem_realistic_anthro1
    COMMAND ${_checker_realistic} --category anthro1 --variable SO2 --rtol 2e-5
    WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}
  )
  add_test(
    NAME conservation_cece_met_catchem_realistic_dust
    COMMAND ${_checker_realistic} --category dust --variable sep --rtol 3e-3
    WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}
  )
  set_tests_properties(
    conservation_cece_met_catchem_realistic_anthro1 conservation_cece_met_catchem_realistic_dust
    PROPERTIES
      FIXTURES_REQUIRED "mockdata_realistic;cece_met_catchem_realistic_run"
      LABELS ci
      TIMEOUT 120
  )

  # Issue #16: the checked-in weather-model set (configs/wm/catchem_cece_control) run as it is, from a directory
  # staged the way a regression test stages one (tools/stage_wm_run.py): the set's YAML files copied unmodified,
  # the driver template with its placeholders filled, CATChem's production science config with all seven
  # processes, the driver's run-directory defaults for the three path keys, no hosted imports (so the run
  # sequence is the pushed one line for line and there is no conservation check: the realistic run keeps those).
  # Label `wm`, not `ci`: 336 s on the cloud builder (74 s for the realistic run) and 1.44 GB per PET (0.93 GB).
  # `tools/ci.sh test wm` runs it there. The generated resolution of the set (C96)
  # does not matter to the files: the grid comes from the host field.
  set(_wm_set catchem_cece_control)
  set(_run_wm ${CMAKE_CURRENT_BINARY_DIR}/run_wm_${_wm_set}_np6)
  set(_stage_wm ${UV_EXECUTABLE} run --no-sync python -m tools.stage_wm_run
      --set configs/wm/${_wm_set} --out ${_run_wm}
      --manifest data/C48_realistic/manifest.yaml --extdata data/C48_realistic/ExtData
      --streams ${PROJECT_BINARY_DIR}/gen/C48_realistic/streams)
  add_test(NAME stage_wm_${_wm_set} COMMAND ${_stage_wm} WORKING_DIRECTORY ${PROJECT_SOURCE_DIR})
  set_tests_properties(
    stage_wm_${_wm_set}
    PROPERTIES
      FIXTURES_REQUIRED "mockdata_realistic;genconfigs_realistic"
      FIXTURES_SETUP wm_${_wm_set}_staged
      LABELS wm
      TIMEOUT 120
  )
  # The run passes on its summary line; mpiexec's exit status goes to wm_exit_status.txt for exit_wm_<set>, so a
  # run that completes and then dies in its exit handlers shows as exactly that: this test green, that one red.
  # The file of an earlier run is removed first: a run killed by the TIMEOUT writes none.
  add_test(
    NAME proto_wm_${_wm_set}
    COMMAND
      bash -c "rm -f wm_exit_status.txt; set -o pipefail; \"$0\" \"$@\" 2>&1 | tee wm_stdout.txt; echo mpiexec exit status $? > wm_exit_status.txt"
      ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 6 ${MPIEXEC_PREFLAGS} $<TARGET_FILE:esmApp> ufs.configure
    WORKING_DIRECTORY ${_run_wm}
  )
  set_tests_properties(
    proto_wm_${_wm_set}
    PROPERTIES
      FIXTURES_REQUIRED wm_${_wm_set}_staged
      FIXTURES_SETUP wm_${_wm_set}_run
      LABELS wm
      ENVIRONMENT_MODIFICATION "OMP_NUM_THREADS=unset:"
      PASS_REGULAR_EXPRESSION "wm_${_wm_set}: completed 120 steps"
      TIMEOUT 900
  )
  # (the status is written as a phrase: log_check tests a match as a CMake value, and a bare 0 is false)
  file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/exit_wm.spec "require mpiexec exit status 0$\n")
  add_test(
    NAME exit_wm_${_wm_set}
    COMMAND
      ${CMAKE_COMMAND} -DRUN_DIR=${_run_wm} -DFILES=wm_exit_status.txt
      -DSPEC=${CMAKE_CURRENT_BINARY_DIR}/exit_wm.spec -P ${_log_check}
  )
  set_tests_properties(
    exit_wm_${_wm_set}
    PROPERTIES FIXTURES_REQUIRED wm_${_wm_set}_run LABELS wm TIMEOUT 60
  )
  # The failure case of that check: a recorded status of 134 (SIGABRT, what the first runs of the set left, while
  # esmApp carried ESMF twice; top-level CMakeLists.txt).
  set(_exit_fails_dir ${CMAKE_CURRENT_BINARY_DIR}/exit_wm_nonzero_fails)
  file(WRITE ${_exit_fails_dir}/wm_exit_status.txt "mpiexec exit status 134\n")
  add_test(
    NAME exit_wm_nonzero_fails
    COMMAND
      ${CMAKE_COMMAND} -DRUN_DIR=${_exit_fails_dir} -DFILES=wm_exit_status.txt
      -DSPEC=${CMAKE_CURRENT_BINARY_DIR}/exit_wm.spec -P ${_log_check}
  )
  set_tests_properties(
    exit_wm_nonzero_fails
    PROPERTIES LABELS "ci;wm" PASS_REGULAR_EXPRESSION "log_check: required but not found" TIMEOUT 60
  )
  # CATChem reports a failing process on stdout and carries on (the first run of the set printed 1.66 million
  # `CATChem ERROR: Error in running GOCART Chem_SettlingSimple scheme` blocks, 624 MB, and still completed:
  # the optics tables had no 550 nm channel), so a completed run proves nothing about its processes without this.
  file(
    WRITE ${CMAKE_CURRENT_BINARY_DIR}/stdout_wm.spec
    [=[
forbid CATChem ERROR
forbid ERROR LOCATION
forbid is an invalid value
forbid double free or corruption
forbid exited on signal
require wm_catchem_cece_control: completed 120 steps
]=]
  )
  add_test(
    NAME stdout_wm_${_wm_set}
    COMMAND
      ${CMAKE_COMMAND} -DRUN_DIR=${_run_wm} -DFILES=wm_stdout.txt
      -DSPEC=${CMAKE_CURRENT_BINARY_DIR}/stdout_wm.spec -P ${_log_check}
  )
  set_tests_properties(
    stdout_wm_${_wm_set}
    PROPERTIES FIXTURES_REQUIRED wm_${_wm_set}_run LABELS wm TIMEOUT 300
  )
  # After the run the directory still holds the set unmodified: the five YAML files and the species file byte
  # for byte, the configure file starting with the filled template.
  add_test(NAME verify_wm_${_wm_set} COMMAND ${_stage_wm} --verify WORKING_DIRECTORY ${PROJECT_SOURCE_DIR})
  set_tests_properties(
    verify_wm_${_wm_set}
    PROPERTIES FIXTURES_REQUIRED wm_${_wm_set}_run LABELS wm TIMEOUT 120
  )

  # The PET-log evidence, as for the realistic run without the hosted imports: the 33 imports connected, bound
  # and reference-shared, the 18 gridded categories import-fed, no emission file opened, CECE's grid from the
  # host field, the two ATM phases, the configuration files found by the driver's run-directory defaults, and
  # the memory ceiling of the production processes (1.44 GB per PET measured on the cloud builder; 3.40 GB while
  # the settling scheme failed on every call).
  file(
    WRITE ${CMAKE_CURRENT_BINARY_DIR}/log_wm_${_wm_set}.spec
    [=[
require driver: CATChem config CATChem_new_config.yml, field mapping CATChem_field_mapping.yml
require driver: CECE config cece.yaml
require CATChem: RealizeAccepted removed 0 unconnected optional import\(s\)
require CATChem: category anthro1: 3 field\(s\) read from NUOPC imports
require CATChem: category anthro2: 3 field\(s\) read from NUOPC imports
require CATChem: category ship: 4 field\(s\) read from NUOPC imports
require CATChem: category fire: 1 field\(s\) read from NUOPC imports
require CATChem: category bio: 4 field\(s\) read from NUOPC imports
require CATChem: category gmi: 3 field\(s\) read from NUOPC imports
require CATChem: category dms: 1 field\(s\) read from NUOPC imports
require CATChem: category dust: 4 field\(s\) read from NUOPC imports
require CATChem: category fengsha: 1 field\(s\) read from NUOPC imports
require CATChem: 33 emission import\(s\) bound to emission fields
count 33 Realized export field "[a-z0-9_]+_[A-Za-z0-9_]+" \(48x48x127\)
count 33 CATChem: emission import extemis_[a-z0-9_]+_[A-Za-z0-9_]+ ShareStatusField=shared
forbid ShareStatusField=not shared
forbid Removed unconnected export field
forbid owns an open corner ring
require catchem_emis_init: extemis source=nuopc_import
require catchem_emis_update: Emission data updated
forbid CATChem: emission open category=
forbid AQMIO: open iomode=read path=
forbid MET: hosted import
forbid Unrecognized emission field units
forbid Unrecognized vertical_dist option
forbid DRIVER FATAL
forbid ^[0-9]+ [0-9.]+ ERROR
require \[Cap\] grid source: host field ATM/inst_tracer_mass_frac \(tileCount=6, local DE [0-5] of tile [1-6]\)
require \[Cap\] Host grid block: .* \(48x48\), corners 49x49
forbid \[Cap\] Parent grid:
forbid \[Cap\] No parent grid or mesh
require MET: run phase phase1 \(exports filled\) first entered at 2021-03-22T06:00:00
require MET: run phase phase2 \(tracers taken, hosted imports totalled\) first entered at 2021-03-22T06:00:00
forbid MET: no default run phase
require \[Cap\] Kokkos OpenMP threads from the ESMF VM: 1 PE\(s\) on this PET
below 1700000 wm_catchem_cece_control: memory per PET max virt [0-9]+ KB phys ([0-9]+) KB \(at end\)
]=]
  )
  add_test(
    NAME log_wm_${_wm_set}
    COMMAND
      ${CMAKE_COMMAND} -DRUN_DIR=${_run_wm} -DSPEC=${CMAKE_CURRENT_BINARY_DIR}/log_wm_${_wm_set}.spec -P ${_log_check}
  )
  set_tests_properties(
    log_wm_${_wm_set}
    PROPERTIES FIXTURES_REQUIRED wm_${_wm_set}_run LABELS wm TIMEOUT 120
  )
endif()
