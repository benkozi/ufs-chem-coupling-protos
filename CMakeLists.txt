cmake_minimum_required(VERSION 3.28)
project(ufs-chem-coupling-protos LANGUAGES C CXX Fortran)

enable_testing()
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

find_package(MPI REQUIRED)

# Container launches run as root on oversubscribed CPUs. The image sets PROTOS_MPIEXEC_PREFLAGS
# ("--allow-run-as-root;--oversubscribe"); it is copied into MPIEXEC_PREFLAGS when that cache
# variable is empty, so `cmake -S . -B build` needs no flags in the container and stays clean on
# a host that does not set it (HPC). An explicit -DMPIEXEC_PREFLAGS always wins.
if(NOT MPIEXEC_PREFLAGS AND DEFINED ENV{PROTOS_MPIEXEC_PREFLAGS})
  set(
    MPIEXEC_PREFLAGS
    "$ENV{PROTOS_MPIEXEC_PREFLAGS}"
    CACHE STRING
    "Flags placed before the executable in mpiexec launches (from PROTOS_MPIEXEC_PREFLAGS)"
    FORCE
  )
endif()
message(STATUS "MPIEXEC_PREFLAGS: ${MPIEXEC_PREFLAGS}")

# ESMF through esmf.mk (ESMFMKFILE), the same module CATChem uses; defines the `esmf` target.
find_package(ESMF REQUIRED)
message(STATUS "ESMF ${ESMF_VERSION} from ${ESMFMKFILE}")

# CATChem (cc_nuopc and its archive closure) from the image's build tree; defines CATChem::cc_nuopc.
include(catchem_build_tree)

add_subdirectory(tests/smoke)
add_subdirectory(tests/mockdata)
add_subdirectory(protos/met_catchem)
