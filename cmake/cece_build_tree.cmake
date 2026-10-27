# CECE from its build tree in the image: the imported target CECE::cece_nuopc_cap.
#
# The image builds CECE once (Dockerfile stage `cece`, ENABLE_ESMF=ON) and keeps the build tree at
# ${CECE_ROOT}/build. CECE installs only libcece_core.so and libcece_driver.so plus headers (no CMake package,
# no install(EXPORT)); the NUOPC cap archive, the HELM libraries, Kokkos Kernels and the Fortran modules stay
# in the build tree. The link set below is the one CECE's own cece_nuopc_app links (build.ninja of the image,
# task 4 audit): the cap archive, the two shared CECE libraries, HELM AMIO (shared), AXIS, Kokkos Kernels,
# DAGR, CONF (static), spack yaml-cpp (shared), HELM LOGS (shared), HALO, Kokkos, OpenMP, the MPI C++ bindings
# and TICK. The static HELM archives go into one linker group because AXIS, HALO and CONF reference each other.
# The shared libraries that are not on the image's LD_LIBRARY_PATH (libcece_*.so, libamio.so, liblogs.so)
# are reached through an rpath carried on the target, so a by-hand `mpirun ../esmApp` needs no environment.
#
# yaml-cpp: CECE links spack's shared libyaml-cpp 0.8.0 (namespace YAML), ESMF embeds its own copy and CATChem
# vendors one under the namespace catchem_yaml; the three meet in esmApp for the first time here (design.md
# reminder; checked with nm/ldd at the first link).
#
# CECE_ROOT: cache variable, default from the environment (the image sets CECE_ROOT=/opt/cece).

set(CECE_ROOT "$ENV{CECE_ROOT}" CACHE PATH "CECE source/build/install root (the image sets /opt/cece)")
if(NOT CECE_ROOT)
  message(FATAL_ERROR "CECE_ROOT is not set: point it at the CECE root holding build/ (the image sets /opt/cece)")
endif()

set(_cece_build "${CECE_ROOT}/build")
set(_cece_mod_dir "${_cece_build}/modules")
set(_cece_helm "${_cece_build}/extern/helm/libs")

set(_cece_cap "${_cece_build}/src/driver/nuopc/libcece_nuopc_cap.a")
set(_cece_shared "${_cece_build}/libcece_driver.so" "${_cece_build}/libcece_core.so")
set(_cece_amio "${_cece_helm}/amio/libamio.so")
set(_cece_logs "${_cece_helm}/logs/liblogs.so")
set(
  _cece_static
  "${_cece_helm}/axis/libaxis.a"
  "${_cece_build}/_deps/kokkos-kernels-build/libkokkoskernels.a"
  "${_cece_helm}/dagr/libdagr.a"
  "${_cece_helm}/conf/libconf.a"
  "${_cece_helm}/halo/libhalo.a"
  "${_cece_helm}/tick/libtick.a"
)

foreach(_lib IN LISTS _cece_cap _cece_shared _cece_amio _cece_logs _cece_static)
  if(NOT EXISTS "${_lib}")
    message(FATAL_ERROR "CECE library not found: ${_lib} (is CECE_ROOT=${CECE_ROOT} a CECE tree built with ENABLE_ESMF=ON?)")
  endif()
endforeach()
if(NOT EXISTS "${_cece_mod_dir}/cece_cap_mod.mod")
  message(FATAL_ERROR "CECE module directory not found: ${_cece_mod_dir}/cece_cap_mod.mod")
endif()

# Kokkos (one install for both models, /opt/kokkos) and the spack yaml-cpp CECE links.
if(NOT Kokkos_ROOT AND DEFINED ENV{KOKKOS_ROOT})
  set(Kokkos_ROOT "$ENV{KOKKOS_ROOT}")
endif()
find_package(Kokkos REQUIRED)
find_library(_cece_yamlcpp NAMES yaml-cpp REQUIRED DOC "spack yaml-cpp 0.8.0 (the one CECE links)")
find_package(OpenMP REQUIRED COMPONENTS CXX)
find_package(MPI REQUIRED COMPONENTS Fortran C CXX)

add_library(CECE::cece_nuopc_cap INTERFACE IMPORTED)
set_target_properties(
  CECE::cece_nuopc_cap
  PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${_cece_mod_dir}"
    INTERFACE_LINK_LIBRARIES
      "${_cece_cap};${_cece_shared};${_cece_amio};-Wl,--start-group;${_cece_static};-Wl,--end-group;${_cece_yamlcpp};${_cece_logs};Kokkos::kokkos;OpenMP::OpenMP_CXX;MPI::MPI_CXX;esmf;MPI::MPI_Fortran"
    INTERFACE_LINK_OPTIONS
      "LINKER:-rpath,${_cece_build};LINKER:-rpath,${_cece_helm}/amio;LINKER:-rpath,${_cece_helm}/logs"
)
message(STATUS "CECE build tree: ${_cece_build} (cap archive, 2 shared CECE libraries, 8 HELM/Kokkos-Kernels libraries, modules in ${_cece_mod_dir})")

unset(_cece_cap)
unset(_cece_shared)
unset(_cece_amio)
unset(_cece_logs)
unset(_cece_static)
unset(_cece_helm)
unset(_cece_build)
unset(_cece_mod_dir)
