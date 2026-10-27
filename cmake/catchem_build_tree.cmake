# CATChem from its build tree in the image: the imported target CATChem::cc_nuopc.
#
# The image builds CATChem once (Dockerfile stage `catchem`, CATCHEM_BUILD_NUOPC=ON) and keeps the build tree
# at ${CATCHEM_ROOT}/build. There is no CMake package to find: CATChem has no install(EXPORT), and its
# install tree lacks the API, core, GOCART2G and utilities archives. libcc_nuopc.a itself holds only the nine
# cap/driver objects; everything it references lives in the archives below, which CATChem links as PRIVATE
# dependencies of the cc_nuopc target (CATChem CMakeLists.txt:552-629). An executable outside CATChem's tree
# therefore links the whole set. The archives go into one linker group because the process libraries, the
# core and the vendored yaml-cpp reference each other in both directions. ESMF (with netCDF-Fortran and the
# C++ runtime, from esmf.mk) and the MPI Fortran, C and C++ bindings (libesmf.a uses the C++ bindings) come
# last. The vendored yaml-cpp is compiled in the namespace catchem_yaml, so it cannot clash with the yaml-cpp
# ESMF embeds (namespace YAML).
#
# CATCHEM_ROOT: cache variable, default from the environment (the image sets CATCHEM_ROOT=/opt/catchem).

set(CATCHEM_ROOT "$ENV{CATCHEM_ROOT}" CACHE PATH "CATChem source/build/install root (the image sets /opt/catchem)")
if(NOT CATCHEM_ROOT)
  message(FATAL_ERROR "CATCHEM_ROOT is not set: point it at the CATChem root holding build/ (the image sets /opt/catchem)")
endif()

set(_catchem_build "${CATCHEM_ROOT}/build")
set(_catchem_mod_dir "${_catchem_build}/include")

# Dependency order: the cap, the Fortran API, the process libraries, the C++ core, the externals.
set(
  _catchem_archives
  "${_catchem_build}/libcc_nuopc.a"
  "${_catchem_build}/src/api/libCATChem.a"
  "${_catchem_build}/src/process/seasalt/libCATChem_process_seasalt.a"
  "${_catchem_build}/src/process/dust/libCATChem_process_dust.a"
  "${_catchem_build}/src/process/drydep/libCATChem_process_drydep.a"
  "${_catchem_build}/src/process/wetdep/libCATChem_process_wetdep.a"
  "${_catchem_build}/src/process/settling/libCATChem_process_settling.a"
  "${_catchem_build}/src/process/so4chem/libCATChem_process_so4chem.a"
  "${_catchem_build}/src/process/carbchem/libCATChem_process_carbchem.a"
  "${_catchem_build}/src/core/libCATChem_core_cpp.a"
  "${_catchem_build}/src/external/libcatchem_utilities.a"
  "${_catchem_build}/src/external/libGOCART2G.a"
  "${_catchem_build}/_deps/yaml-cpp-build/libyaml-cpp.a"
)

foreach(_archive IN LISTS _catchem_archives)
  if(NOT EXISTS "${_archive}")
    message(FATAL_ERROR "CATChem archive not found: ${_archive} (is CATCHEM_ROOT=${CATCHEM_ROOT} a CATChem tree built with CATCHEM_BUILD_NUOPC=ON?)")
  endif()
endforeach()
if(NOT EXISTS "${_catchem_mod_dir}/cc_nuopc.mod")
  message(FATAL_ERROR "CATChem module directory not found: ${_catchem_mod_dir}/cc_nuopc.mod")
endif()

find_package(MPI REQUIRED COMPONENTS Fortran C CXX)

add_library(CATChem::cc_nuopc INTERFACE IMPORTED)
set_target_properties(
  CATChem::cc_nuopc
  PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${_catchem_mod_dir}"
    INTERFACE_LINK_LIBRARIES
      "-Wl,--start-group;${_catchem_archives};-Wl,--end-group;esmf;MPI::MPI_Fortran;MPI::MPI_C;MPI::MPI_CXX"
)
message(STATUS "CATChem build tree: ${_catchem_build} (13 archives, modules in ${_catchem_mod_dir})")

unset(_catchem_archives)
unset(_catchem_build)
unset(_catchem_mod_dir)
