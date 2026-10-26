# syntax=docker/dockerfile:1
# One image for everything: the spack base (ESMF 8.9.1, OpenMPI, netCDF, yaml-cpp), the Python tooling,
# a shared Kokkos, ESMF-enabled CECE and CATChem built from the submodules, and the prototype sources.
# Stages form a DAG: python, kokkos, cece and catchem all derive from base and only final depends on all
# of them, so a change to one piece rebuilds that piece alone. CI and tools/ci.sh bind-mount the live
# checkout at /repo and run there; /opt/protos/src is a convenience copy.
#
#   BUILDX_BUILDER=cloud-bkrlps-cece-builder docker compose build
#   docker compose run --rm ci sh -c 'cmake -S . -B build && cmake --build build && ctest --test-dir build -L ci'
ARG BASE_IMAGE=noaaepic/ufschem-spack-base-ubuntu-gcc-13-dev:latest
ARG UV_VERSION=0.10.7
ARG KOKKOS_VERSION=5.1.1

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---------------------------------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS base
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# The spack environment view. The base image activates it only in interactive shells (~/.bashrc);
# every CI command runs non-interactively, so the paths are set here, explicitly.
ENV SPACK_VIEW=/opt/ufschem/spack-stack/spack/var/spack/environments/ufschem/.spack-env/view
ENV ESMFMKFILE=${SPACK_VIEW}/lib/esmf.mk \
    PATH=${SPACK_VIEW}/bin:${PATH} \
    CMAKE_PREFIX_PATH=${SPACK_VIEW} \
    PKG_CONFIG_PATH=${SPACK_VIEW}/lib/pkgconfig \
    LD_LIBRARY_PATH=${SPACK_VIEW}/lib

# MPI as root, oversubscribed: for every mpirun in the container (CECE's own tests, ad-hoc runs) ...
ENV OMPI_ALLOW_RUN_AS_ROOT=1 \
    OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 \
    OMPI_MCA_rmaps_base_oversubscribe=1
# ... and for CTest: the top-level CMakeLists.txt copies this into MPIEXEC_PREFLAGS when that is empty,
# so `cmake -S . -B build` needs no flags inside the container and stays clean on a host without it.
ENV PROTOS_MPIEXEC_PREFLAGS="--allow-run-as-root;--oversubscribe"

# uv, with the venv outside any bind mount. UV_PYTHON pins the image's interpreter (3.12.3), so no
# managed-interpreter download; PRE_COMMIT_HOME keeps the isolated hook environments in the image.
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PRE_COMMIT_HOME=/opt/pre-commit \
    UV_LINK_MODE=copy \
    UV_PYTHON=/usr/bin/python3

# ---------------------------------------------------------------------------------------------------
# Python tooling: keyed on the three files only. The throwaway `git init` satisfies pre-commit's
# in-a-repo requirement while the hook environments (pre-commit-hooks, conventional-pre-commit) bake.
FROM base AS python
WORKDIR /opt/protos-tooling
COPY pyproject.toml uv.lock .pre-commit-config.yaml ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project
RUN --mount=type=cache,target=/root/.cache/uv \
    git init -q . \
    && uv run --no-sync pre-commit install-hooks \
    && rm -rf .git

# ---------------------------------------------------------------------------------------------------
# One Kokkos for both models (the base image ships none). CECE would otherwise FetchContent its own on
# every rebuild, and CATChem, once CATCHEM_ENABLE_KOKKOS is turned on, must link the same Kokkos as CECE.
FROM base AS kokkos
ARG KOKKOS_VERSION
RUN curl -fsSL "https://github.com/kokkos/kokkos/releases/download/${KOKKOS_VERSION}/kokkos-${KOKKOS_VERSION}.tar.gz" \
      | tar -xz -C /tmp \
    && cmake -S "/tmp/kokkos-${KOKKOS_VERSION}" -B /tmp/kokkos-build -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/opt/kokkos \
      -DCMAKE_CXX_STANDARD=20 \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DKokkos_ENABLE_SERIAL=ON \
      -DKokkos_ENABLE_OPENMP=ON \
    && cmake --build /tmp/kokkos-build -j"$(nproc)" \
    && cmake --install /tmp/kokkos-build \
    && rm -rf /tmp/kokkos-*

# ---------------------------------------------------------------------------------------------------
# CECE (ENABLE_ESMF=ON): cece_nuopc_cap (static) and cece_nuopc_app. The test suite stays off (GTest and
# RapidCheck are not in the base image; the submodule's own CI runs it). The build tree is kept: CECE
# exports no CMake package, so the prototype driver links the cap and its .mod files from the tree.
# configure.log keeps the "CECE dependency" summary (Kokkos, yaml-cpp, ESMF, netCDF provenance).
# netCDF: spack's netcdf-c (4.9.3, the one ESMF links) installs no CMake package file, and apt's 4.9.2
# under /usr does, so CECE's find_package(netCDF CONFIG) would pick apt's. The config lookup is disabled
# and CECE's fallback uses the netCDF root FindESMF derives from esmf.mk; HELM's AXIS uses pkg-config,
# which PKG_CONFIG_PATH (base stage) points at the view.
FROM base AS cece
COPY --from=kokkos /opt/kokkos /opt/kokkos
COPY extern/CECE /opt/cece/src
RUN cmake -S /opt/cece/src -B /opt/cece/build -G Ninja \
      -DCMAKE_BUILD_TYPE=RelWithDebInfo \
      -DENABLE_ESMF=ON \
      -DCECE_BUILD_TESTING=OFF \
      -DCECE_MPIEXEC_CONTAINER_FLAGS=ON \
      -DKokkos_ROOT=/opt/kokkos \
      -DCMAKE_DISABLE_FIND_PACKAGE_netCDF=ON \
      -DCMAKE_INSTALL_PREFIX=/opt/cece/install \
      2>&1 | tee /opt/cece/configure.log \
    && cmake --build /opt/cece/build -j"$(nproc)" \
    && cmake --install /opt/cece/build

# ---------------------------------------------------------------------------------------------------
# CATChem (CATCHEM_BUILD_NUOPC=ON): the self-contained cc_nuopc archive plus NUOPC_CATChem_cap. Kokkos
# and MUSICA stay off (the NUOPC cap does not need them; /opt/kokkos is there when a later task flips
# Kokkos on). In NUOPC builds CATChem uses its vendored, symbol-isolated yaml-cpp by design.
# Makefiles, not Ninja: NUOPC_CATChem_cap and cc_nuopc compile the same sources and both emit their
# .mod files into build/include, which Ninja rejects as duplicate outputs (make tolerates it).
FROM base AS catchem
COPY extern/CATChem /opt/catchem/src
RUN cmake -S /opt/catchem/src -B /opt/catchem/build -G "Unix Makefiles" \
      -DCMAKE_BUILD_TYPE=RelWithDebInfo \
      -DCATCHEM_BUILD_NUOPC=ON \
      -DCATCHEM_BUILD_TESTING=OFF \
      -DCATCHEM_ENABLE_KOKKOS=OFF \
      -DCATCHEM_BUILD_MUSICA=OFF \
      -DCMAKE_INSTALL_PREFIX=/opt/catchem/install \
      2>&1 | tee /opt/catchem/configure.log \
    && cmake --build /opt/catchem/build -j"$(nproc)" \
    && cmake --install /opt/catchem/build

# ---------------------------------------------------------------------------------------------------
FROM base AS final
COPY --from=python /opt/venv /opt/venv
COPY --from=python /opt/pre-commit /opt/pre-commit
COPY --from=kokkos /opt/kokkos /opt/kokkos
COPY --from=cece /opt/cece /opt/cece
COPY --from=catchem /opt/catchem /opt/catchem
ENV KOKKOS_ROOT=/opt/kokkos \
    CECE_ROOT=/opt/cece \
    CATCHEM_ROOT=/opt/catchem
# The prototype sources, last: the only layer a prototype edit invalidates.
COPY . /opt/protos/src
WORKDIR /repo
CMD ["/bin/bash"]
