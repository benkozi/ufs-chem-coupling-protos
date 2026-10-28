# Prototype met_catchem: the NUOPC driver, the analytic MET provider and CATChem's cap on the
# cubed-sphere grid read from the task-2 mosaic. See runbook.md section 9. The same esmApp serves every
# prototype (the config's EARTH_component_list picks the children; cece_met lives in protos/cece_met/).

add_executable(
  esmApp
  esmApp.F90
  proto_driver.F90
  proto_met.F90
  proto_met_analytic.F90
  proto_manifest.F90
  proto_totals.F90
)
target_link_libraries(esmApp PRIVATE CATChem::cc_nuopc CECE::cece_nuopc_cap)
set_target_properties(
  esmApp
  PROPERTIES
    LINKER_LANGUAGE Fortran
    Fortran_MODULE_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}/mod
)
target_compile_options(esmApp PRIVATE $<$<COMPILE_LANGUAGE:Fortran>:-Wall;-ffree-line-length-none>)

# The driver configs: one per resolution, absolute paths, the coupling step (STEP_SECONDS, read from the
# defaults file by the top-level CMakeLists.txt) so the run sequence and the manifest cannot drift.
set(PROTOS_ROOT ${PROJECT_SOURCE_DIR})
set(_configs ${PROJECT_SOURCE_DIR}/configs/prescribed/met_catchem)
# The driver configs, one per resolution, on the prescribed science config (diagnostics on).
set(CATCHEM_CONFIG ${_configs}/CATChem_new_config.yml)
set(GRID_SOURCE mosaic)
foreach(RES IN ITEMS C48 C96)
  configure_file(esm.configure.in ${CMAKE_CURRENT_BINARY_DIR}/esm.${RES}.configure @ONLY)
endforeach()
# The file-free debug variant (analytic grid, no CATChem diagnostics) for machines whose amd64 emulation
# cannot run the image's HDF5 (Rosetta has no x87); never part of ci.
set(CATCHEM_CONFIG ${_configs}/CATChem_new_config_nodiag.yml)
set(RES C48)
set(GRID_SOURCE analytic)
configure_file(esm.configure.in ${CMAKE_CURRENT_BINARY_DIR}/esm.C48.local.configure @ONLY)
message(STATUS "met_catchem: coupling step ${STEP_SECONDS} s; configs in ${CMAKE_CURRENT_BINARY_DIR}")

# Every manifest value the components read, printed by the Fortran reader.
add_test(
  NAME met_catchem_manifest_dump
  COMMAND
    ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 1 ${MPIEXEC_PREFLAGS}
    $<TARGET_FILE:esmApp> --dump-manifest ${PROJECT_SOURCE_DIR}/data/C48/manifest.yaml
)
set_tests_properties(
  met_catchem_manifest_dump
  PROPERTIES
    FIXTURES_REQUIRED mockdata
    LABELS ci
    PASS_REGULAR_EXPRESSION "clock.n_levels=127"
    TIMEOUT 120
)

# A prototype run: <ranks> PETs on the <res> config in its own run directory (CATChem writes catchem_diag/
# there, ESMF its PET logs). <steps> is the pass criterion printed by esmApp.
function(met_catchem_run name res ranks steps fixture label timeout)
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
      PASS_REGULAR_EXPRESSION "met_catchem: completed ${steps} steps"
      TIMEOUT ${timeout}
  )
endfunction()

met_catchem_run(proto_met_catchem C48 6 120 mockdata ci 1800)
met_catchem_run(proto_met_catchem_local C48.local 6 120 mockdata local 1800)
met_catchem_run(proto_met_catchem_np12 C48 12 120 mockdata ci 1800)
met_catchem_run(proto_met_catchem_c96 C96 12 120 mockdata_c96 c96 7200)

# The PET rule: 4 ranks cannot give one DE per PET on six tiles; the driver says so and stops.
file(MAKE_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}/run_C48_np4)
add_test(
  NAME proto_met_catchem_bad_petcount
  COMMAND
    ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 4 ${MPIEXEC_PREFLAGS}
    $<TARGET_FILE:esmApp> ${CMAKE_CURRENT_BINARY_DIR}/esm.C48.configure
  WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}/run_C48_np4
)
set_tests_properties(
  proto_met_catchem_bad_petcount
  PROPERTIES
    FIXTURES_REQUIRED mockdata
    LABELS ci
    PASS_REGULAR_EXPRESSION "PET count 4 must be a multiple of 6"
    # The message must arrive through a normal termination: an MPI_Abort races the stdout forwarding
    # (develop run 40597634701 lost PET 0's line), so the abort banner fails the test.
    FAIL_REGULAR_EXPRESSION "MPI_ABORT|ORTE_ERROR_LOG"
    TIMEOUT 300
)
