# Prototype met_catchem (task 3): the NUOPC driver, the analytic MET provider and CATChem's cap on the
# cubed-sphere grid read from the task-2 mosaic. See runbook.md section 9.

add_executable(
  esmApp
  esmApp.F90
  proto_driver.F90
  proto_met.F90
  proto_met_analytic.F90
  proto_manifest.F90
)
target_link_libraries(esmApp PRIVATE CATChem::cc_nuopc)
set_target_properties(
  esmApp
  PROPERTIES
    LINKER_LANGUAGE Fortran
    Fortran_MODULE_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}/mod
)
target_compile_options(esmApp PRIVATE $<$<COMPILE_LANGUAGE:Fortran>:-Wall;-ffree-line-length-none>)

# The driver configs: one per resolution, absolute paths, the coupling step from the defaults file so the
# run sequence and the manifest cannot drift.
file(STRINGS ${PROJECT_SOURCE_DIR}/tools/defaults/manifest.yaml _step_line REGEX "^  step_seconds:")
string(REGEX REPLACE "^  step_seconds: *([0-9]+).*$" "\\1" STEP_SECONDS "${_step_line}")
if(NOT STEP_SECONDS MATCHES "^[0-9]+$")
  message(FATAL_ERROR "cannot read clock.step_seconds from tools/defaults/manifest.yaml (got '${_step_line}')")
endif()
set(PROTOS_ROOT ${PROJECT_SOURCE_DIR})
set(_science_template ${PROJECT_SOURCE_DIR}/configs/prescribed/met_catchem/CATChem_new_config.yml.in)
# The science config (diagnostics on) and the driver configs, one per resolution.
set(DIAG_ENABLED true)
set(CATCHEM_CONFIG ${CMAKE_CURRENT_BINARY_DIR}/CATChem_new_config.yml)
configure_file(${_science_template} ${CATCHEM_CONFIG} @ONLY)
set(GRID_SOURCE mosaic)
foreach(RES IN ITEMS C48 C96)
  configure_file(esm.configure.in ${CMAKE_CURRENT_BINARY_DIR}/esm.${RES}.configure @ONLY)
endforeach()
# The file-free debug variant (analytic grid, no CATChem diagnostics) for machines whose amd64 emulation
# cannot run the image's HDF5 (Rosetta has no x87); never part of ci.
set(DIAG_ENABLED false)
set(CATCHEM_CONFIG ${CMAKE_CURRENT_BINARY_DIR}/CATChem_new_config_nodiag.yml)
configure_file(${_science_template} ${CATCHEM_CONFIG} @ONLY)
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
    TIMEOUT 300
)
