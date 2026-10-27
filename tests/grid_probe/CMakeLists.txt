# grid_probe, label `probe` (never part of ci): prints what each PET can read from the task-2 mosaic grid
# (tile of the local DE, centre and corner coordinate bounds, the DistGrid's per-DE blocks, the area check).
# Run on the cloud builder with `tools/ci.sh test probe -V` (ctest prints the probe's stdout only in verbose mode).

add_executable(grid_probe grid_probe.F90)
target_link_libraries(grid_probe PRIVATE esmf MPI::MPI_Fortran MPI::MPI_CXX)
set_target_properties(grid_probe PROPERTIES LINKER_LANGUAGE Fortran Fortran_MODULE_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}/mod)
target_compile_options(grid_probe PRIVATE $<$<COMPILE_LANGUAGE:Fortran>:-Wall;-ffree-line-length-none>)

foreach(RANKS IN ITEMS 6 12)
  set(_run_dir ${CMAKE_CURRENT_BINARY_DIR}/run_np${RANKS})
  file(MAKE_DIRECTORY ${_run_dir})
  add_test(
    NAME grid_probe_np${RANKS}
    COMMAND
      ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} ${RANKS} ${MPIEXEC_PREFLAGS}
      $<TARGET_FILE:grid_probe> ${PROJECT_SOURCE_DIR}/data/C48/fix/C48_mosaic.nc ${PROJECT_SOURCE_DIR}/data/C48/fix/
    WORKING_DIRECTORY ${_run_dir}
  )
  set_tests_properties(
    grid_probe_np${RANKS}
    PROPERTIES
      FIXTURES_REQUIRED mockdata
      LABELS probe
      PASS_REGULAR_EXPRESSION "grid_probe: done"
      TIMEOUT 600
  )
endforeach()
