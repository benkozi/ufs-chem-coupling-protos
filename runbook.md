# Runbook

How to build the image, run each CI job locally, and what the image contains. Commands here are the ones that were actually run; see `design.md` in the tasking repository for the why.

## 1. Base image

`noaaepic/ufschem-spack-base-ubuntu-gcc-13-dev:latest` (the `develop` prerelease build of [ufs-chem-container](https://github.com/ufs-community/ufs-chem-container)), index digest `sha256:bcca9993e1d88824c0f620fd43c3d29575b588ea06fad9f004dd0ea9ef460b61`, built 2026-09-23, 5.1 GB, **`linux/amd64` only**. Ubuntu 24.04, spack-stack `37c009d` (2.1.1), spack environment `ufschem`. Inspected 2026-10-26 with `spack find -lv` and `esmf.mk` inside the image.

| Component | Version | Provider | Notes |
| --- | --- | --- | --- |
| ESMF | 8.9.1 (`ESMF_VERSION_STRING=8.9.1 beta snapshot`, git `6a09112`) | spack | `+shared +mpi +netcdf +openmp +external-parallelio`, `ESMF_COMM=openmpi`, `ESMF_PIO=1`, `ESMF_YAMLCPP=1` (yaml-cpp compiled into libesmf), `ESMF_MOAB=1`; both `libesmf.a` and `libesmf.so` installed |
| OpenMPI | 4.1.6 | apt (`libopenmpi-dev`), spack external | |
| GCC / gfortran | 13.3.0 | apt | the only spack compiler |
| netCDF-C | 4.9.3 | spack | `+mpi`; apt's `libnetcdf-dev` 4.9.2 is also installed (see caveats) |
| netCDF-Fortran | 4.6.1 | spack | apt's `libnetcdff-dev` also installed |
| HDF5 | 1.14.6 | spack | `+mpi` |
| parallelio / pnetcdf | 2.6.5 / 1.14.0 | spack | |
| yaml-cpp | 0.8.0 | spack | shared; satisfies CECE's `find_package(yaml-cpp 0.8 CONFIG)` |
| CMake | 3.28.3 | apt | Ninja 1.11 also installed |
| Python | 3.12.3 | apt, spack external | no uv in the base |
| Kokkos | absent | | added by this image, see below |
| GTest / RapidCheck | absent | | CECE's and CATChem's test suites are not built |

Paths:

- Spack environment view (everything spack-built): `/opt/ufschem/spack-stack/spack/var/spack/environments/ufschem/.spack-env/view` (`bin/`, `lib/`, `include/`, `lib/cmake/`).
- `ESMFMKFILE=<view>/lib/esmf.mk`.
- The base image sets these only through `~/.bashrc` (`spack env activate ufschem`), i.e. for interactive shells. This image's `Dockerfile` sets `SPACK_VIEW`, `ESMFMKFILE`, `PATH`, `CMAKE_PREFIX_PATH` and `LD_LIBRARY_PATH` as `ENV`, so `docker run … sh -c` sees them too.

## 2. What this image adds (`Dockerfile`)

Stages: `base` → `python`, `kokkos`, `cece`, `catchem` (independent, built in parallel) → `final`.

| Piece | Where | How |
| --- | --- | --- |
| uv 0.10.7 | `/usr/local/bin/uv` | copied from `ghcr.io/astral-sh/uv:0.10.7`; `UV_PYTHON=/usr/bin/python3` (3.12.3), `UV_PROJECT_ENVIRONMENT=/opt/venv`, `PRE_COMMIT_HOME=/opt/pre-commit` |
| Python tooling | `/opt/venv`, `/opt/pre-commit` | `uv sync --frozen --no-install-project` from `pyproject.toml`/`uv.lock`; pre-commit hook environments pre-baked |
| Kokkos 5.1.1 | `/opt/kokkos` (`KOKKOS_ROOT`) | release tarball, `-DCMAKE_CXX_STANDARD=20 -DKokkos_ENABLE_SERIAL=ON -DKokkos_ENABLE_OPENMP=ON -DCMAKE_POSITION_INDEPENDENT_CODE=ON`, Release |
| CECE (`extern/CECE`) | `/opt/cece/src`, `/opt/cece/build`, `/opt/cece/install` (`CECE_ROOT=/opt/cece`) | Ninja, RelWithDebInfo, `-DENABLE_ESMF=ON -DCECE_BUILD_TESTING=OFF -DCECE_MPIEXEC_CONTAINER_FLAGS=ON -DKokkos_ROOT=/opt/kokkos -DCMAKE_DISABLE_FIND_PACKAGE_netCDF=ON` (see caveats: netCDF); configure output in `/opt/cece/configure.log` |
| CATChem (`extern/CATChem`) | `/opt/catchem/src`, `/opt/catchem/build`, `/opt/catchem/install` (`CATCHEM_ROOT=/opt/catchem`) | **Unix Makefiles** (Ninja rejects the duplicate `.mod` outputs of `NUOPC_CATChem_cap` and `cc_nuopc`), RelWithDebInfo, `-DCATCHEM_BUILD_NUOPC=ON -DCATCHEM_BUILD_TESTING=OFF -DCATCHEM_ENABLE_KOKKOS=OFF -DCATCHEM_BUILD_MUSICA=OFF`; configure output in `/opt/catchem/configure.log` |
| Prototype sources | `/opt/protos/src` | `COPY .`, the last layer; CI and `tools/ci.sh` bind-mount the live checkout at `/repo` instead |

MPI as root in the container: `OMPI_ALLOW_RUN_AS_ROOT=1`, `OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1`, `OMPI_MCA_rmaps_base_oversubscribe=1` for any `mpirun`, and `PROTOS_MPIEXEC_PREFLAGS="--allow-run-as-root;--oversubscribe"`, which the top-level `CMakeLists.txt` copies into `MPIEXEC_PREFLAGS` when that cache variable is empty.

Artefacts to expect (checked after the build, section 6):

- `/opt/cece/build/bin/cece_nuopc_app`, `/opt/cece/install/bin/cece_nuopc_app`, `/opt/cece/build/lib/libcece_nuopc_cap.a`
- `/opt/catchem/build/libcc_nuopc.a` and `/opt/catchem/build/include/*.mod` (`cc_nuopc` has no install rule; consumers link the build tree)
- `/opt/kokkos/lib/cmake/Kokkos/KokkosConfig.cmake`

## 3. Build the image locally

Always with the Docker Build Cloud builder (`cloud-bkrlps-cece-builder`, amd64 node; the base image has no arm64 variant):

```bash
tools/ci.sh build
```

which is `BUILDX_BUILDER=cloud-bkrlps-cece-builder docker compose build`; the image lands in the local daemon as `ufs-chem-coupling-protos:dev`. Fallback if compose does not load it: `docker buildx build --builder cloud-bkrlps-cece-builder --platform linux/amd64 --load -t ufs-chem-coupling-protos:dev .`. A single stage can be built for debugging with `--target cece` (or `catchem`, `kokkos`, `python`) and `--progress=plain`.

The build context is the checkout minus `.dockerignore` (`.git`, build trees, `.venv`, `design/`, `data/`): about 70 MB with both submodules.

## 4. Run a CI job locally

Each runs the identical command string the workflow runs, inside `ufs-chem-coupling-protos:dev`, with the checkout bind-mounted at `/repo` (`compose.yaml`, service `ci`):

```bash
tools/ci.sh pre-commit
```

```bash
tools/ci.sh pytest
```

```bash
tools/ci.sh ctest
```

| Job | Command inside the container |
| --- | --- |
| pre-commit | `git config --global --add safe.directory /repo && uv sync --frozen && uv run pre-commit run --all-files` |
| pytest | `git config --global --add safe.directory /repo && uv sync --frozen && uv run pytest tools/tests --cov --cov-report=term --cov-report=html:coverage-html` |
| ctest | `cmake -S . -B build && cmake --build build && ctest --test-dir build --output-on-failure -L ci` |

`ctest -L ci` runs the two smoke tests in `tests/smoke/`: `mpi_smoke` (2 ranks, expects `ranks=2`) and `esmf_version` (expects `ESMF_VERSION_STRING=8.9.1…`). The `build/` directory on the bind mount is created by the container; it is git-ignored.

On the Mac, outside the container: `uv sync --frozen`, `uv run pre-commit run --all-files`, `uv run pytest tools/tests --cov` (host Python is 3.14 per `.python-version`; the image uses its own 3.12.3).

## 5. Submodules

| Path | Repository | Branch | Pinned commit |
| --- | --- | --- | --- |
| `extern/CATChem` | `https://github.com/bbakernoaa/CATChem.git` | `feature/rework` | `84532349` |
| `extern/CECE` | `https://github.com/ufs-community/CECE.git` | `develop` | `25f7209` |

`extern/CECE` carries `extern/helm` (NOAA-EMC/HELM) and `extern/yaml-cpp`; `extern/CATChem` carries `src/external/GOCART` and `src/external/musica` (unused with `CATCHEM_BUILD_MUSICA=OFF`).

```bash
git submodule update --init --recursive
```

To move a pin: check out the new commit inside `extern/<name>`, rebuild the image (only that model's stage rebuilds), commit the gitlink. When a task needs changes in CATChem or CECE, cut a branch on `benkozi/<name>` from the pinned commit and re-point `.gitmodules`.

## 6. Build and test record (task 1, 2026-10-26, this Mac, cloud builder amd64 node)

Stage build times on the cloud builder (each stage once, cold): `kokkos` ~10 s, `python` ~10 s, `catchem` 92 s (Makefiles; the first Ninja attempt failed with `multiple rules generate include/catchem_nuopc_lengths.mod`), `cece` 190–214 s (the first attempt resolved netCDF to apt's 4.9.2 CMake package; fixed as described in the caveats). A `docker compose build` with every stage cached, including the load of the image into the local daemon, takes about 3 minutes; the loaded image is 1.95 GB of content (8.97 GB on disk as reported by `docker images`): `/opt/cece` 2.4 GB (source + build tree + install), `/opt/catchem` 322 MB, `/opt/venv` 125 MB, `/opt/pre-commit` 27 MB, `/opt/kokkos` 8 MB.

Artefacts, checked non-interactively (`docker compose run --rm ci sh -c …`): `$ESMFMKFILE` is the view's `esmf.mk`; `/opt/cece/build/bin/cece_nuopc_app`, `/opt/cece/install/bin/cece_nuopc_app`, `/opt/cece/build/src/driver/nuopc/libcece_nuopc_cap.a`, `/opt/catchem/build/libcc_nuopc.a` with the `.mod` files in `/opt/catchem/build/include`, `/opt/kokkos/lib/cmake/Kokkos/KokkosConfig.cmake`; `uv 0.10.7`.

Dependency provenance from the configure logs: CECE — Kokkos `system 5.1.1 (/opt/kokkos)`, mdspan `FetchContent (mdspan-0.6.0)`, yaml-cpp `system 0.8.0` (view), ESMF via `esmf.mk`, netCDF `system library (<view>/lib/libnetcdf.so)`, HELM AMIO `netCDF located via nc-config: <view>/lib/libnetcdf.so`, HELM AXIS via pkg-config, MPI `/usr/bin/mpicxx`. CATChem — NetCDF `<view>/include` 4.9.3 (Fortran component), ESMF `<view>/lib/libesmf.a` 8.9.1, `yaml-cpp not found on system, using internal version` (the vendored, symbol-isolated copy, by design in NUOPC builds).

Jobs run locally in the image: `tools/ci.sh ctest` — `mpi_smoke` and `esmf_version` both pass (about 0.5 s each, under emulation on Apple Silicon); with `PROTOS_MPIEXEC_PREFLAGS` and the `OMPI_*` variables unset, `MPIEXEC_PREFLAGS` is empty and `mpi_smoke` fails with OpenMPI's "run as root" refusal, which is the red half of the preflag bridge. `tools/ci.sh pytest` — 1 passed, coverage 100% (no statements yet), HTML report written. `tools/ci.sh pre-commit` — every hook passes; note that `pre-commit run --all-files` only sees git-tracked files, so on a checkout with uncommitted new files the per-file hooks skip them (the tools were also run directly on every new file: ruff check/format, mypy, vulture, codespell, yamlfix, yamllint, `uv lock --check`).

Cache stability: after a `pyproject.toml` change, a rebuild re-ran the `python` stage (about 10 s) and `final` only, with `kokkos`, `cece` and `catchem` reported `CACHED`; after prototype-only edits (`runbook.md`, `tests/smoke/*`), every step was `CACHED` except `final`'s `COPY . /opt/protos/src`, and the whole `docker compose build` took 10 s.

Two fixes the smoke tests forced: `ESMF_Initialize` takes `logkindflag=` in ESMF 8.9 (not `defaultLogType=`), and an executable linking the static `libesmf.a` needs `MPI::MPI_CXX` as well as `MPI::MPI_Fortran` (ESMF uses the MPI C++ bindings and `esmf.mk` leaves `-lmpi_cxx` to the compiler wrappers).

## 7. Caveats

- **Apple Silicon**: the container runs under x86_64 emulation. pre-commit and pytest are fine; MPI launches are slow under emulation, and CI (`ubuntu-latest`, native amd64) is the authority for `ctest`.
- **Two netCDF-C installs** in the base image (apt 4.9.2 under `/usr`, spack 4.9.3 in the view; ESMF links the spack one). Spack's netcdf-c installs no CMake package file and apt's does, so a plain `find_package(netCDF CONFIG)` picks apt's regardless of `CMAKE_PREFIX_PATH` (CECE did, in the first task-1 build). The image therefore sets `PKG_CONFIG_PATH` to the view (HELM's AXIS uses pkg-config, `netcdf.pc` → 4.9.3) and CECE is configured with `-DCMAKE_DISABLE_FIND_PACKAGE_netCDF=ON`, which sends CECE to its `NetCDF_ROOT` fallback (the path FindESMF derives from `esmf.mk`) and HELM's AMIO to its `nc-config` fallback (the view's `nc-config`, parallel-enabled). CATChem's own `FindNetCDF.cmake` finds the view directly. Any new CMake project in the image must do the same; check its configure log.
- **ESMF embeds yaml-cpp**; CECE links spack's shared 0.8.0 and CATChem its vendored symbol-isolated copy. No conflict in separate builds; watch the first single-executable link (task 3/6).
- **Fork pull requests** run CI with a read-only token: they read the GHCR build cache but cannot write it.
