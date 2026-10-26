# Smoke tests, label `ci`: they prove what every prototype depends on (an MPI launch as root in the
# container, ESMF found and linked) and keep the ctest CI job green before the first prototype lands.

add_executable(mpi_smoke mpi_smoke.c)
target_link_libraries(mpi_smoke PRIVATE MPI::MPI_C)
add_test(
  NAME mpi_smoke
  COMMAND
    ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 2 ${MPIEXEC_PREFLAGS}
    $<TARGET_FILE:mpi_smoke>
)
set_tests_properties(
  mpi_smoke
  PROPERTIES
    PASS_REGULAR_EXPRESSION "ranks=2"
    LABELS ci
    TIMEOUT 120
)

add_executable(esmf_version esmf_version.F90)
# MPI::MPI_CXX: static libesmf.a uses the MPI C++ bindings (libmpi_cxx), which the Fortran
# target does not carry and esmf.mk leaves to the compiler wrappers.
target_link_libraries(esmf_version PRIVATE esmf MPI::MPI_Fortran MPI::MPI_CXX)
add_test(
  NAME esmf_version
  COMMAND
    ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 1 ${MPIEXEC_PREFLAGS}
    $<TARGET_FILE:esmf_version>
)
set_tests_properties(
  esmf_version
  PROPERTIES
    PASS_REGULAR_EXPRESSION "ESMF_VERSION_STRING=8\\.9\\.1"
    LABELS ci
    TIMEOUT 120
)
