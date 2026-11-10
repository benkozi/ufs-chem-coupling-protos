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
# One ESMF per process. FindESMF (the weather model's module) prefers the static libesmf.a, while CECE's shared
# libraries (libcece_driver.so, libcece_core.so) are linked against libesmf.so, so an executable holding both
# CATChem and CECE carried ESMF twice. The symbols the shared libraries reference resolve to the executable's
# copy, both copies register a destructor for the same global, and a non-empty one is freed twice at exit: the
# first run of the checked-in weather-model set ended, after `completed 120 steps`, with `double free or
# corruption (!prev)` in ~vector<MPI_Datatype> (ESMCI::VMK::customType) called from libesmf.so's finalizer,
# exit status 134 (runbook section 16). The prototypes therefore link the shared library when the ESMF
# installation has one; the link flags esmf.mk gives for the static library stay on the target (harmless).
get_filename_component(_esmf_mk_dir "${ESMFMKFILE}" DIRECTORY)
find_library(PROTOS_ESMF_SHARED NAMES libesmf.so PATHS ${ESMF_LIBSDIR} ${_esmf_mk_dir} NO_DEFAULT_PATH)
if(PROTOS_ESMF_SHARED)
  set_target_properties(esmf PROPERTIES IMPORTED_LOCATION "${PROTOS_ESMF_SHARED}")
  message(STATUS "ESMF linked shared: ${PROTOS_ESMF_SHARED}")
else()
  message(WARNING "no libesmf.so next to ${ESMFMKFILE}: the executables link the static ESMF, and one that also loads CECE's shared libraries carries ESMF twice")
endif()

# CATChem (cc_nuopc and its archive closure) from the image's build tree; defines CATChem::cc_nuopc.
include(catchem_build_tree)
# CECE (the NUOPC cap, the core and HELM) from the image's build tree; defines CECE::cece_nuopc_cap.
include(cece_build_tree)

# The manifest clock the prototypes render into their configs (tools/defaults/manifest.yaml is the single
# source; a mismatch between a run sequence and the mock data's step would otherwise go unnoticed).
file(STRINGS ${PROJECT_SOURCE_DIR}/tools/defaults/manifest.yaml _step_line REGEX "^  step_seconds:")
string(REGEX REPLACE "^  step_seconds: *([0-9]+).*$" "\\1" STEP_SECONDS "${_step_line}")
if(NOT STEP_SECONDS MATCHES "^[0-9]+$")
  message(FATAL_ERROR "cannot read clock.step_seconds from tools/defaults/manifest.yaml (got '${_step_line}')")
endif()
message(STATUS "manifest coupling step: ${STEP_SECONDS} s")

add_subdirectory(tests/smoke)
add_subdirectory(tests/mockdata)
add_subdirectory(tests/grid_probe)
add_subdirectory(tests/genconfigs)
add_subdirectory(protos/met_catchem)
add_subdirectory(protos/cece_met)
add_subdirectory(protos/cece_met_catchem)

# The hard copy of the configuration set a weather-model test stages (issue #16): generated into the build tree
# on every build, compared with configs/wm/catchem_cece_control/ by a ci test, refreshed by an explicit target.
include(wm_config_set)
protos_wm_config_set(
  NAME catchem_cece_control
  MANIFEST_DEFAULTS tools/defaults/manifest_realistic.yaml
  GEN_DEFAULTS tools/defaults/gen_configs_realistic.yaml
  BASE_CONFIG extern/CATChem/tests/Configs/Default/CATChem_new_config.yml
  RESOLUTION C96
)
