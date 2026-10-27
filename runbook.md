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

On Apple silicon the prototype runs of `ctest` cannot execute in the local daemon (section 7, Rosetta), so the same tests also run as a **build step on the cloud builder's native amd64 node**: the Dockerfile's `test` stage (`FROM final`, `COPY . /repo`, `uv sync`, `cmake`, `ctest -L ${CTEST_LABEL}`), built with `--no-cache-filter test` so the step always executes, its output being the build log:

```bash
tools/ci.sh test ci
```

```bash
tools/ci.sh test c96
```

A third word onward is passed to ctest (`tools/ci.sh test ci -R proto_met_catchem$`). The fix files live in a BuildKit cache mount on the builder (`id=protos-data`), so the EMC download happens once per node; on failure the step prints the ESMF log errors of the prototype run directories. The image people run is still `final` (`compose.yaml` pins `target: final`).

| Job | Command inside the container |
| --- | --- |
| pre-commit | `git config --global --add safe.directory /repo && uv sync --frozen && uv run pre-commit run --all-files` |
| pytest | `git config --global --add safe.directory /repo && uv sync --frozen && uv run pytest tools/tests --cov --cov-report=term --cov-report=html:coverage-html` |
| ctest | `git config --global --add safe.directory /repo && uv sync --frozen && cmake -S . -B build && cmake --build build && ctest --test-dir build --output-on-failure -L ci` |

`ctest -L ci` runs the two smoke tests in `tests/smoke/` (`mpi_smoke`, 2 ranks, expects `ranks=2`; `esmf_version`, expects `ESMF_VERSION_STRING=8.9.1…`), the `make_mock_data` fixture test in `tests/mockdata/` (section 8), which downloads the C48 fix files on first run, and the task-3 prototype tests in `protos/met_catchem/` (section 9: the manifest dump, the PET-rule failure case and the C48 runs on 6 and 12 PETs). The `build/` and `data/` directories on the bind mount are created by the container; both are git-ignored and outside the Docker build context.

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

- **Apple Silicon**: the container runs under x86_64 emulation. pre-commit and pytest are fine; MPI launches are slow under emulation, and CI (`ubuntu-latest`, native amd64) is the authority for `ctest`. **With Docker Desktop's Rosetta emulation, nothing in the image that initializes HDF5 runs at all**: any `nc_open` (even the view's `ncdump -h`) dies with `SIGILL`, because `libhdf5.so.310` (spack, `linux-zen2`) executes the x87 instruction `ffreep` in its type initialization and Rosetta does not emulate x87 (found in task 3, 2026-10-27). So `ESMF_GridCreateMosaic` and CATChem's aqmio writer cannot run on such a Mac; the two C48 prototype runs of `ctest -L ci` fail there with that signal and pass in CI. Local alternatives: `tools/ci.sh test ci` (section 4: the tests as a build step on the cloud builder's native amd64 node), `ctest -L local` (section 9, the file-free variant in the local daemon), or switching Docker Desktop from Rosetta to QEMU emulation (Settings, needs a restart; untested).
- **Two netCDF-C installs** in the base image (apt 4.9.2 under `/usr`, spack 4.9.3 in the view; ESMF links the spack one). Spack's netcdf-c installs no CMake package file and apt's does, so a plain `find_package(netCDF CONFIG)` picks apt's regardless of `CMAKE_PREFIX_PATH` (CECE did, in the first task-1 build). The image therefore sets `PKG_CONFIG_PATH` to the view (HELM's AXIS uses pkg-config, `netcdf.pc` → 4.9.3) and CECE is configured with `-DCMAKE_DISABLE_FIND_PACKAGE_netCDF=ON`, which sends CECE to its `NetCDF_ROOT` fallback (the path FindESMF derives from `esmf.mk`) and HELM's AMIO to its `nc-config` fallback (the view's `nc-config`, parallel-enabled). CATChem's own `FindNetCDF.cmake` finds the view directly. Any new CMake project in the image must do the same; check its configure log.
- **ESMF embeds yaml-cpp**; CECE links spack's shared 0.8.0 and CATChem its vendored symbol-isolated copy. No conflict in separate builds; watch the first single-executable link (task 3/6).
- **Fork pull requests** run CI with a read-only token: they read the GHCR build cache but cannot write it.
- **Python 3.14 on the Mac hides annotation errors.** The host interpreter evaluates annotations lazily; the image's 3.12.3 evaluates them at import. Every module under `tools/` therefore starts with `from __future__ import annotations`, and `tools/ci.sh pytest` (3.12) is the check that counts: a subscripted runtime class in an annotation (`netCDF4.Variable[object]`) passed the host suite and failed only in the image (task 2).
- **`pre-commit run --all-files` sees tracked files only.** On a checkout with new, not yet `git add`ed files the per-file hooks skip them; run the tools directly on new files or stage them first.

## 8. Mock data (task 2)

One offline step produces everything a prototype run needs except meteorology (task 3's MET component computes it analytically at run time) and configuration files (task 5's generator; tasks 3 and 4 use hand-written files under `configs/prescribed/`). Its parameter set is the checked-in `tools/defaults/manifest.yaml` (the manifest minus generator, resolution and fix files, with paths relative to `--out`); the command-line options override the clock and the inventory grid, and every environment knob goes through `tools/settings.py` (`PROTOS_*`, table below):

```bash
uv run python -m tools.make_mock_data --resolution C48 --out data/C48
```

```bash
uv run python -m tools.make_mock_data --resolution C96 --out data/C96
```

Run from the repository root (`python -m`, not the script path: `uv` does not install the project, so `tools.*` is importable only from the root). Inside the image the same command runs through the baked venv as `uv run --no-sync python -m tools.make_mock_data …`.

| Option | Default | Meaning |
| --- | --- | --- |
| `--resolution {C48,C96}` | required | C48 for CI, C96 for the prototype |
| `--out PATH` | required | output directory, git-ignored (`data/`) |
| `--defaults PATH` | `tools/defaults/manifest.yaml` (`$PROTOS_DEFAULTS`) | the parameter set to start from |
| `--start ISO8601` | the defaults file's `clock.start` (`2021-03-22T06:00:00Z`, `catchem_control`) | run start with timezone; naive stamps are a usage error |
| `--hours N` | the defaults file's `clock.duration_hours` (24) | run length |
| `--step-seconds N` | the defaults file's `clock.step_seconds` (720) | coupling step; must divide the run length |
| `--levels N` | the defaults file's `clock.n_levels` (127) | model levels (`NPZ`); the `lev` size of `gmi.nc` |
| `--inventory-resolution DEG` | the defaults file's `grids.inventory` (1°) | spacing of the 2-D inventory grid; `360/DEG` must be an integer |
| `--fix-url URL` | the settings' `fix_url` (`$PROTOS_FIX_URL`, the EMC server) | directory URL template with `{resolution}` |
| `--log-level LEVEL` | `$PROTOS_LOG_LEVEL` or `INFO` | logging through `tools/log.py` |

Exit status: 0 on success, 1 when the fetch or the generation fails (logged at `ERROR`, traceback at `DEBUG`), 2 for a usage error (including a missing or invalid defaults file).

To run with a different parameter set, copy `tools/defaults/manifest.yaml`, edit it (the file is the manifest schema below without `generator`, `resolution` and `fix`; keep CATChem's `NO` quoted, an unquoted `NO` is boolean false in YAML 1.1), and pass `--defaults` or set `PROTOS_DEFAULTS`. The defaults file never changes behind your back: a test pins its content by digest, so editing it means updating that test on purpose.

### Outputs

```
data/<res>/fix/                      the 13 EMC fix files, unchanged: <res>_mosaic.nc, <res>_grid.tile1-6.nc, <res>_grid_spec.tile1-6.nc
data/<res>/inventories/anthro.nc     hourly, 25 records: NO SO2 POC TERP ISOP PEC PSO4 (kg m-2 s-1), land-masked
data/<res>/inventories/fire.nc       hourly, 25 records: NO SO2 CO (kg m-2 s-1), FRP (MW), land-masked
data/<res>/inventories/gmi.nc        monthly, 12 records x <levels>: oh (cm-3), h2o2, no3 (mol mol-1), unmasked, 2.5 x 2 degree grid
data/<res>/inventories/dms.nc        monthly, 12 records: dms (nmol L-1), ocean-masked
data/<res>/inventories/dust.nc       static, 1 record: clayfrac sandfrac sep (1), uthres (m s-1), land-masked
data/<res>/inventories/fengsha.nc    monthly, 12 records: albedo_drag (1), land-masked
data/<res>/inventories/landmask.nc   land_fraction (float32, [0,1]) and land_mask (int8) on the inventory grid
data/<res>/inventories/totals.yaml   sum of field x area per variable, record and level
data/<res>/manifest.yaml             the run manifest (schema below)
```

Every inventory file is CF: 1-D `lon`/`lat` centres with `lon_bnds`/`lat_bnds`, `area` in m² (exact spherical cell areas, R = 6371 km), `time` with `time_bnds`, dimension order `(time, [lev,] lat, lon)`, float32 with deflate level 1. Time axes: hourly files carry 25 centred records from the start to start + 24 h inclusive (`hours since <start>`), so a linearly interpolating reader brackets the last step; monthly files carry the 12 mid-month records of the start year with month bounds (`days since <year>-01-01`); static files carry one record at the start. Variable names are CATChem's file variable names (the keys of `CATChem_emission_AQM.yml`). `gmi.nc` levels are `bottom_up` (index 0 at the surface, `lev:positive = "up"`); flip with the manifest's `level_order`. The fields are closed-form (background plus Gaussian sources, a diurnal factor from local solar time for hourly data, a seasonal factor from the record month for monthly data, `exp(-k/H)` over levels for 3-D data, times the land or ocean fraction) with every constant in the manifest and no random numbers, so a run is reproducible from the manifest alone.

### Fix files: source, validation, cache

Source: `https://ftp.emc.ncep.noaa.gov/static_files/public/UFS/GFS/fix/fix_fv3/<res>/` (plain directory listing; files dated October 2016; C48 set 3.7 MB, C96 set 13.8 MB). `tools/fixfiles.py` streams each file to `<name>.part` and renames it only when the byte count equals `Content-Length`; a short read or HTTP error removes the partial file, logs an `ERROR` and the command exits 1. Files that are present and valid are skipped, so a second run is offline. Validation per file mirrors what the consumers read: the mosaic must have `ntiles = 6`, `gridfiles` naming the six tiles and `ncontact = 12`; a `grid` tile must carry a `grid_tile_spec` variable with `projection = cube_gnomonic` and `(2N+1)²` `geographic_longitude`/`geographic_latitude` supergrids (that is how `ESMF_GridCreateMosaic` finds them); a `grid_spec` tile must carry `(N, N)` `grid_lont`/`grid_latt`/`area` and `(N+1, N+1)` `grid_lon`/`grid_lat`, with the tile's area within 1 % of a sixth of the sphere. The mosaic's `gridlocation` is the generating machine's absolute path; the driver passes `tileFilePath=data/<res>/fix/` to ESMF instead.

Local cache: `data/<res>/fix/`. CI: the `ctest` job caches `data/C48/fix` with `actions/cache` under the key `fixfiles-C48-<hash of tools/fixfiles.py>`, so only a change to the fetch module re-downloads. The URL template, download timeout, chunk size and the area tolerance are settings (`PROTOS_FIX_URL`, `PROTOS_DOWNLOAD_TIMEOUT_S`, `PROTOS_DOWNLOAD_CHUNK_BYTES`, `PROTOS_AREA_TOLERANCE`).

### Test fixture (downloaded on demand, never committed)

No NetCDF file is committed (`*.nc` is git-ignored). The validator tests read the real `C48_mosaic.nc`, `C48_grid.tile1.nc` and `C48_grid_spec.tile1.nc`, which the session-scoped `c48_fixture_dir` fixture in `tools/tests/conftest.py` downloads once into the settings' `fixture_dir` (`data/fixtures/C48/`, `PROTOS_FIXTURE_DIR`; 627 KB) through the real opener and reuses afterwards; every other download in the suite is mocked, and an autouse fixture makes any other use of `urllib.request.urlopen` fail. The `pytest` CI job caches `data/fixtures/C48` under the key `fixtures-C48-<hash of tools/fixfiles.py>`. The six-tile and failure cases use tiny synthetic tiles (`n = 4`) written in `tmp_path`. To refresh the cache, delete `data/fixtures/C48` and run the suite again.

### CTest fixtures

`tests/mockdata/CMakeLists.txt` registers `make_mock_data` (`FIXTURES_SETUP mockdata`, `LABELS ci`, `TIMEOUT 900`) running the C48 generation from the repository root through `uv run --no-sync`, and `make_mock_data_c96` (`FIXTURES_SETUP mockdata_c96`, `LABELS c96`, `TIMEOUT 1800`), which CI never runs. A prototype test declares `FIXTURES_REQUIRED mockdata` and `ctest -R proto_<name>` then generates its own inputs.

### Measured (2026-10-26)

| Run | Time | Output |
| --- | --- | --- |
| C96 on the Mac (native arm64, Python 3.14) | 18 s wall (fetch included) | 184 MB: `gmi.nc` 110 MB, `anthro.nc` 34 MB, `fire.nc` 20 MB, `fengsha.nc` 2.9 MB, `dms.nc` 2.3 MB, `dust.nc` 1.4 MB, `landmask.nc` 0.7 MB, fix files 13 MB |
| C48 inside the image (`tools/ci.sh ctest`, amd64 under emulation on the Mac, cold fix-file download) | 15.5 s for the fixture test cold (fetch included), 5.8 s warm (13 files already present, no connection opened); `tools/ci.sh ctest` as a whole about 30 s | 180 MB: inventories 176 MB (same grids as C96), fix files 3.5 MB |

The inventory grids do not depend on the cubed-sphere resolution, so the C48 and C96 inventories are the same size; only `fix/` differs.

### Settings (`tools/settings.py`, environment prefix `PROTOS_`)

One shared object for every environment variable, tunable constant and default-file path; `get_settings()` reads it once per process. No other module reads the environment.

`Settings`

| Field | Env | Type | Default | Description |
| --- | --- | --- | --- | --- |
| `log_level` | `PROTOS_LOG_LEVEL` | `Literal['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']` | `'INFO'` | Level of the project logger (PROTOS_LOG_LEVEL); the CLI's --log-level wins when given. |
| `log_format` | `PROTOS_LOG_FORMAT` | `str` | `'%(asctime)s %(levelname)s %(name)s: %(message)s'` | logging.Formatter format of the single stderr handler. |
| `fix_url` | `PROTOS_FIX_URL` | `str` | `'https://ftp.emc.ncep.noaa.gov/static_files/public/UFS/GFS/fix/fix_fv3/{resolution}/'` | Directory URL template of the FV3 fix files; {resolution} is C48 or C96. |
| `download_timeout_s` | `PROTOS_DOWNLOAD_TIMEOUT_S` | `float` | `120.0` | Socket timeout of each fix-file download, seconds. |
| `download_chunk_bytes` | `PROTOS_DOWNLOAD_CHUNK_BYTES` | `int` | `1048576` | Read size while streaming a download to its .part file. |
| `area_tolerance` | `PROTOS_AREA_TOLERANCE` | `float` | `0.01` | Allowed relative deviation of a grid_spec tile's total area from a sixth of the sphere. |
| `earth_radius_m` | `PROTOS_EARTH_RADIUS_M` | `float` | `6371000.0` | Earth radius used for the inventory cell areas, m (FV3's RADIUS). |
| `cf_conventions` | `PROTOS_CF_CONVENTIONS` | `str` | `'CF-1.8'` | Value of the Conventions attribute written to every NetCDF file. |
| `deflate_level` | `PROTOS_DEFLATE_LEVEL` | `int` | `1` | zlib deflate level of the inventory variables (0 disables compression). |
| `deflate_shuffle` | `PROTOS_DEFLATE_SHUFFLE` | `bool` | `True` | Apply the HDF5 shuffle filter before deflate. |
| `generator_version` | `PROTOS_GENERATOR_VERSION` | `str` | `'0.1.0'` | Version string the generator writes into the manifest. |
| `defaults_file` | `PROTOS_DEFAULTS` | `Path` | `PosixPath('tools/defaults/manifest.yaml')` | YAML file with the default manifest parameters (PROTOS_DEFAULTS); --defaults wins when given. |
| `manifest_filename` | `PROTOS_MANIFEST_FILENAME` | `str` | `'manifest.yaml'` | Name of the manifest written under --out. |
| `totals_filename` | `PROTOS_TOTALS_FILENAME` | `str` | `'totals.yaml'` | Name of the totals file written under the inventories directory. |
| `fix_dirname` | `PROTOS_FIX_DIRNAME` | `str` | `'fix'` | Subdirectory of --out holding the fix files. |
| `fixture_dir` | `PROTOS_FIXTURE_DIR` | `Path` | `PosixPath('data/fixtures/C48')` | Where the test suite caches the real C48 mosaic and tile-1 files it downloads on demand (PROTOS_FIXTURE_DIR). |

### Manifest schema (`data/<res>/manifest.yaml`, `tools/manifest.py`)

Every model forbids unknown keys and every field is described; every model reads and writes itself with `Model.from_yaml(path)` / `model.to_yaml(path)`. `ManifestDefaults` (the checked-in `tools/defaults/manifest.yaml`) carries the `Parameters` fields with relative paths; `Manifest` adds `generator`, `resolution` and `fix`. `cece_mapalgo` is derived from `regrid_method` when omitted (`conserve → consd`, `bilinear → bilinear`, `none → passthrough`) and rejected when inconsistent. `met` is read by task 3's MET component and extended there, never by loose keys.

`Manifest`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `earth_radius_m` | `float` | required | Earth radius used for the inventory cell areas, m (FV3's RADIUS). |
| `clock` | `Clock` | required | Start, length, step and levels of the run. |
| `grids` | `dict[str, LatLonGrid]` | required | Named regular lat-lon grids the inventories are written on. |
| `land_mask` | `LandMask` | required | The analytic land mask. |
| `categories` | `dict[str, Category]` | required | One entry per CATChem emission category. |
| `met` | `MetParams` | required | Analytic meteorology parameters for task 3's MET component. |
| `generator` | `GeneratorInfo` | required | Who wrote the manifest and how. |
| `resolution` | `Literal['C48', 'C96']` | required | Cubed-sphere resolution: C48 (CI) or C96 (prototype default). |
| `fix` | `FixFiles` | required | The FV3 fix files for the resolution. |

`ManifestDefaults`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `earth_radius_m` | `float` | required | Earth radius used for the inventory cell areas, m (FV3's RADIUS). |
| `clock` | `Clock` | required | Start, length, step and levels of the run. |
| `grids` | `dict[str, LatLonGrid]` | required | Named regular lat-lon grids the inventories are written on. |
| `land_mask` | `LandMask` | required | The analytic land mask. |
| `categories` | `dict[str, Category]` | required | One entry per CATChem emission category. |
| `met` | `MetParams` | required | Analytic meteorology parameters for task 3's MET component. |

`GeneratorInfo`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `version` | `str` | required | Version of the mock data generator that wrote this manifest. |
| `generated_at` | `AwareDatetime` | required | UTC time at which the manifest was written. |
| `command` | `str` | required | The command line that produced this run, for the record. |

`FixFiles`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `source_url` | `str` | required | Directory URL the fix files were (or would be) downloaded from. |
| `directory` | `Path` | required | Local directory holding the mosaic and the twelve tile files. |
| `mosaic` | `Path` | required | Path of the GRIDSPEC mosaic file (<res>_mosaic.nc). |
| `grid_tiles` | `list[Path]` | required | The six supergrid files <res>_grid.tileN.nc, tile 1 first. |
| `grid_spec_tiles` | `list[Path]` | required | The six <res>_grid_spec.tileN.nc files with cell centres and areas, tile 1 first. |
| `sizes` | `dict[str, int]` | `{}` | Byte size of each fix file by file name, recorded after validation. |

`Clock`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `start` | `AwareDatetime` | required | Run start (timezone-aware, UTC); the first record of every hourly and static inventory. |
| `duration_hours` | `int` | required | Run length in hours; hourly inventories cover it inclusively. |
| `step_seconds` | `int` | required | Coupling step in seconds (DT_ATMOS and coupling_interval_sec in catchem_control). |
| `n_levels` | `int` | required | Number of model levels (NPZ); the vertical size of 3-D inventories. |

`LatLonGrid`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `dlon` | `float` | required | Longitude spacing in degrees. |
| `dlat` | `float` | required | Latitude spacing in degrees. |
| `lon_first` | `float` | required | Longitude of the first cell centre in degrees east. |
| `lat_first` | `float` | required | Latitude of the first (southernmost) cell centre in degrees north. |
| `nlon` | `int` | required | Number of longitude cells; nlon * dlon must be 360. |
| `nlat` | `int` | required | Number of latitude cells; the outer cell bounds must stay within ±90. |

`LandMask`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `grid` | `str` | required | Name (in `grids`) of the grid the mask is written on. |
| `file` | `Path` | required | NetCDF file holding land_fraction and land_mask. |
| `threshold` | `float` | `0.5` | land_mask = land_fraction >= threshold. |
| `continents` | `list[Continent]` | required | Super-Gaussian land shapes whose clipped sum is the land fraction. |

`Continent`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `lon` | `float` | required | Centre longitude of the land shape in degrees east. |
| `lat` | `float` | required | Centre latitude of the land shape in degrees north. |
| `semi_axis_lon` | `float` | required | Half-width of the shape along its rotated x axis, in degrees. |
| `semi_axis_lat` | `float` | required | Half-height of the shape along its rotated y axis, in degrees. |
| `rotation_deg` | `float` | `0.0` | Counter-clockwise rotation of the shape's axes in degrees. |
| `exponent` | `float` | `4.0` | Super-Gaussian exponent; larger values give flatter tops and sharper edges. |

`Category`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `file` | `Path` | required | NetCDF file holding this category's variables. |
| `cadence` | `Literal['hourly', 'monthly', 'static']` | required | CATChem's `frequency` for the category: hourly, monthly or static. |
| `regrid_method` | `Literal['conserve', 'bilinear', 'none']` | required | CATChem-style regrid method the prototype assigns to the category. |
| `cece_mapalgo` | `Literal['consd', 'bilinear', 'passthrough'] | None` | `None` | CECE stream mapalgo; derived from regrid_method when omitted (conserve→consd, bilinear→bilinear, none→passthrough). |
| `grid` | `str` | required | Name (in `grids`) of the regular lat-lon grid the file is written on. |
| `is_3d` | `bool` | required | True when the variables carry a `lev` dimension of n_levels. |
| `level_order` | `Literal['bottom_up', 'top_down']` | `'bottom_up'` | Ordering of the `lev` dimension: bottom_up (index 0 at the surface) or top_down. |
| `masked_by` | `Literal['land', 'ocean', 'none']` | required | Multiply by the land fraction, the ocean fraction, or nothing. |
| `variables` | `dict[str, Variable]` | required | Variables by their CATChem file variable name. |

`Variable`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `units` | `str` | required | CF units string written to the variable (informational: consumers do not convert). |
| `long_name` | `str` | required | CF long_name written to the variable. |
| `background` | `float` | required | Value added everywhere (inside the mask) before the Gaussian sources. |
| `sources` | `list[GaussianSource]` | required | Gaussian sources summed onto the background. |
| `diurnal` | `Diurnal | None` | `None` | Diurnal cycle applied to hourly data, or null. |
| `seasonal` | `Seasonal | None` | `None` | Seasonal cycle applied to monthly data, or null. |
| `vertical` | `Vertical | None` | `None` | Vertical profile for 3-D data, or null. |

`GaussianSource`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `lon` | `float` | required | Centre longitude of the source in degrees east. |
| `lat` | `float` | required | Centre latitude of the source in degrees north. |
| `sigma_deg` | `float` | required | Standard deviation of the Gaussian in degrees (isotropic in lon/lat). |
| `amplitude` | `float` | required | Peak value of the source in the variable's units before masking and cycles. |

`Diurnal`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `relative_amplitude` | `float` | required | Relative amplitude d of the factor 1 + d cos(2 pi (t_local - peak) / 24 h). |
| `peak_local_hour` | `float` | required | Local solar hour of the daily maximum. |

`Seasonal`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `relative_amplitude` | `float` | required | Relative amplitude s of the factor 1 + s cos(2 pi (month - peak) / 12). |
| `peak_month` | `int` | required | Calendar month (1-12) of the annual maximum. |

`Vertical`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `scale_height_levels` | `float` | required | e-folding depth in level units of exp(-k / H), k = 0 at the surface. |

`MetParams`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `surface_pressure_pa` | `float` | required | Mean sea-level pressure used for the surface pressure field, Pa. |
| `temperature_equator_k` | `float` | required | Surface air temperature at the equator, K. |
| `temperature_pole_k` | `float` | required | Surface air temperature at the poles, K. |
| `lapse_rate_k_per_level` | `float` | required | Temperature decrease per model level below the tropopause, K. |
| `tropopause_level` | `int` | required | Level index (0 at the surface) above which temperature is held constant. |
| `zonal_wind_amplitude_m_s` | `float` | required | Peak zonal wind of the analytic jet, m/s. |
| `pbl_height_m` | `float` | required | Boundary-layer height, m. |
| `soil_moisture` | `float` | required | Volumetric soil moisture, fraction. |
| `land_use_class` | `int` | required | Land-use class index reported over land cells. |
| `diurnal_amplitude_k` | `float` | required | Amplitude of the surface temperature diurnal cycle, K. |
| `diurnal_peak_local_hour` | `float` | required | Local solar hour of the temperature maximum. |

### File models (`tools/inventories.py`)

`FileAttributes`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `Conventions` | `str` | required | CF conventions version (settings `cf_conventions`). |
| `title` | `str` | required | What the file holds. |
| `source` | `str` | `'tools.make_mock_data'` | The generator that wrote the file. |
| `category` | `str | None` | `None` | CATChem category name (inventory files). |
| `cadence` | `Literal['hourly', 'monthly', 'static'] | None` | `None` | hourly, monthly or static (inventory files). |
| `regrid_method` | `Literal['conserve', 'bilinear', 'none'] | None` | `None` | CATChem-style regrid method the prototype assigns (inventory files). |
| `cece_mapalgo` | `Literal['consd', 'bilinear', 'passthrough'] | None` | `None` | CECE stream mapalgo (inventory files). |
| `masked_by` | `Literal['land', 'ocean', 'none'] | None` | `None` | land, ocean or none (inventory files). |
| `resolution` | `Literal['C48', 'C96'] | None` | `None` | Cubed-sphere resolution of the run (inventory files). |

`VariableEncoding`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `zlib` | `bool` | required | Whether the variable is deflated. |
| `complevel` | `int` | required | zlib level, 0-9. |
| `shuffle` | `bool` | required | HDF5 shuffle filter before deflate. |

`Totals`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `categories` | `dict[str, CategoryTotals]` | required | Totals per category name. |

`CategoryTotals`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `file` | `Path` | required | The category file the totals were computed from. |
| `variables` | `dict[str, VariableTotals]` | required | Totals per variable name. |

`VariableTotals`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `units` | `str` | required | Units of the field; the totals are field times area, i.e. these units times m². |
| `records` | `list[list[float]]` | required | Sum of field times cell area, indexed [record][level] (one level for 2-D fields). |

`TimeAxis`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `cadence` | `Literal['hourly', 'monthly', 'static']` | required | Cadence the axis was built for. |
| `values` | `list[float]` | required | Record times in `units` (the centre of each record's interval). |
| `bounds` | `list[tuple[float, float]]` | required | Record intervals in `units`. |
| `units` | `str` | required | CF time units string, e.g. 'hours since 2021-03-22 06:00:00'. |
| `calendar` | `str` | `'standard'` | CF calendar attribute. |
| `stamps` | `list[AwareDatetime]` | required | The record times as datetimes, for evaluating the cycles. |

`totals.yaml` is `Totals`: `categories.<name>.file` and `categories.<name>.variables.<var>.units` / `.records[record][level]` (one level for 2-D fields), the sum of field times cell area.

## 9. Driver skeleton: MET and CATChem on the cubed-sphere grid (task 3)

`protos/met_catchem/` is the first prototype: a NUOPC driver (`esmApp`) running the analytic meteorology provider `MET` and CATChem's cap (`cc_nuopc` from the image) under one run sequence on the task-2 mosaic grid. Sources: `esmApp.F90` (program: dictionary, driver create/run, summary line), `proto_driver.F90` (`NUOPC_Driver`: config, clock, grid, PET rule, children, `runSeq::`), `proto_met.F90` (the `NUOPC_Model` provider), `proto_met_analytic.F90` (the closed-form fields), `proto_manifest.F90` (the `ESMF_HConfig` reader of `manifest.yaml`). CATChem is linked from the image's build tree by `cmake/catchem_build_tree.cmake` (`CATCHEM_ROOT`, the thirteen archives in one linker group plus ESMF and the MPI Fortran, C and C++ bindings; `libcc_nuopc.a` alone holds only the nine driver objects).

### Build and run

Inside the image (`tools/ci.sh ctest` does all of it in the local daemon; `tools/ci.sh test ci` does it on the cloud builder's amd64 node, which is the way on Apple silicon):

```bash
docker compose run --rm ci sh -c 'uv sync --frozen && cmake -S . -B build && cmake --build build && ctest --test-dir build --output-on-failure -L ci'
```

By hand, from the build tree (the configs are rendered there with absolute paths; `esmApp` takes the config path as its only argument, default `./esm.configure`):

```bash
docker compose run --rm ci sh -c 'cd build/protos/met_catchem/run_C48_np6 && mpirun -np 6 ../esmApp ../esm.C48.configure'
```

```bash
docker compose run --rm ci sh -c 'cd build/protos/met_catchem/run_C96_np12 && mpirun -np 12 ../esmApp ../esm.C96.configure'
```

`esmApp --dump-manifest data/C48/manifest.yaml` prints every manifest value the components read. On success PET 0 prints `met_catchem: completed 120 steps to 2021-03-23T06:00:00` (the CTest pass criterion); on failure the message and `ESMF_END_ABORT`, with the detail in `PET*.ESMF_LogFile` in the run directory. The driver logs the tile count, the per-tile decomposition and the one-DE-per-PET check; MET logs the advertised and realized counts, every export's `ShareStatusField`/`ShareStatusGeomObject` (DataInitialize) and a line every ten steps (surface temperature, pressure and the sea-salt maximum of the local DE).

| Config key | Meaning |
| --- | --- |
| `EARTH_component_list: MET CATCHEM` | the children, added in this order (CATChem's Advertise runs first) |
| `<PFX>_petlist_bounds: lo hi` | PET range, `-1 -1` = all PETs; `<PFX>_attributes::` is ingested as NUOPC attributes (`Verbosity = high`) |
| `manifest:` | the task-2 `data/<res>/manifest.yaml`; `data_root:` is the directory its relative paths are relative to (where `make_mock_data` ran) |
| `field_dictionary:` | `configs/prescribed/met_catchem/fd_protos.yaml`, loaded with `NUOPC_FieldDictionarySetup` before the driver exists |
| `catchem_config_file:` | the rendered science config (`build/protos/met_catchem/CATChem_new_config.yml`); `catchem_field_mapping_file:` the submodule's `CATChem_field_mapping.yml` |
| `grid_source: mosaic` | `ESMF_GridCreateMosaic` from the fix files (`tileFilePath` = the fix directory); `analytic` = `ESMF_GridCreateCubedSphere` (debug mode, see below) |
| `regdecomp_per_tile: nx ny` | optional; product must equal PET count / 6; default `1 petCount/6` (192 PETs: `4 8`, the `catchem_control` layout) |
| `runSeq::` | `@720 / MET / MET -> CATCHEM / CATCHEM / CATCHEM -> MET / @`, the step from `tools/defaults/manifest.yaml` at configure time |

PET counts must be multiples of 6 (one DE per PET on six tiles); otherwise `met_catchem: PET count N must be a multiple of 6 (one DE per PET on six tiles)` and an abort. The CATChem cap's own check only logs, which is why the driver enforces it.

### What runs

- **MET** advertises the 43 imports of the mapping (every entry that is not optional or has `advertise: true`) as exports with `"will provide"`, `SharePolicyField="share"`, `SharePolicyGeomObject="share"`, and CATChem's five exports as its own imports (NUOPC never prunes a model's unconnected exports and the cap needs them realized); every field is reference-shared (`43 of 43 exports are reference-shared`). Shapes: 2-D at centre; 3-D with nz, nz+1 (`vertical_axis: interface`) or `met.n_soil_layers` (`soil_layer`) ungridded levels, k = 1 at the surface; the rank-4 tracer field `(nz, ntracers)` with the Array-Info keys `tracerNames`/`tracerUnits` from `met.tracers`. Fields are recomputed in DataInitialize and every Advance from the manifest's `met` parameters, the grid centre coordinates and `ESMF_FieldRegridGetArea` (cell areas in square radians, scaled by the manifest's Earth radius squared; the global sum over 4πR² is logged as a check of the grid's corners): a quadratic-sigma column (`pressure_top_pa`), a lapse-rate temperature profile, hypsometric geopotential **in m² s⁻²** (the cap divides by g0), a cos² jet, a cloud profile, `sphum` decaying as σ²; surface temperature with a diurnal cycle in local solar time, orography from the land fraction, shortwave and heat fluxes from the solar zenith angle; land/ocean/ice/snow/vegetation/soil fields from the manifest's super-Gaussian continents (the task-2 formula in Fortran); roughness **in cm** (the cap multiplies by 0.01). The tracer export persists CATChem's previous-step chemistry (copied back from the import side) and recomputes `sphum`. Exports are stamped at step start (`label_TimestampExport` override) and MET's `label_CheckImport` is a no-op (CATChem stamps its exports at step end and never marks them `Updated` at initialization).
- **CATChem** runs `configs/prescribed/met_catchem/CATChem_new_config.yml.in` as rendered: the submodule's `CATChem_species.yml` by absolute path, sea salt (`geos12`) as the only process, `extemis: activate: false` (the Fortran reader defaults it to true when absent), aqmio diagnostics hourly into `<run dir>/catchem_diag/` (`diag_list`: the five sea-salt bins; aqmio writes one file per tile, `catchem_YYYYMMDD_HHMMSS.tileN.nc`, so a 24 h run leaves 144 files). The cap maps `sphum` as host-owned (QV) and the sea-salt bins as chemical tracers.
- **Manifest additions** (`MetParams`, `tools/defaults/manifest.yaml`, digest pin updated): `n_soil_layers`, `pressure_top_pa`, `orography_max_m`, `specific_humidity_surface`, `cloud_fraction_max`, `friction_velocity_{land,ocean}_m_s`, `roughness_length_{land,ocean}_cm`, `shortwave_peak_w_m2`, `sensible_heat_peak_w_m2`, `latent_heat_peak_w_m2`, `aerodynamic_conductance_s_m`, `canopy_resistance_s_m`, `leaf_area_index`, `vegetation_fraction`, `sea_ice_latitude_deg`, `snow_latitude_deg`, `soil_type_class`, `tracers` (`name`, `units`; `sphum` exactly once, names unique). A manifest written before task 3 lacks them and fails the Fortran reader by key name; regenerate with `make_mock_data` (the CTest fixture does).

### Tests

| Test | Label | What |
| --- | --- | --- |
| `met_catchem_manifest_dump` | `ci` | `esmApp --dump-manifest` on `data/C48/manifest.yaml`, expects `clock.n_levels=127` |
| `proto_met_catchem_bad_petcount` | `ci` | 4 PETs, expects the PET-rule message |
| `proto_met_catchem`, `proto_met_catchem_np12` | `ci` | C48, 6 and 12 PETs, 24 h in 120 steps, mosaic grid, diagnostics on; run directories `build/protos/met_catchem/run_C48_np{6,12}/` |
| `proto_met_catchem_c96` | `c96` | C96, 12 PETs, `FIXTURES_REQUIRED mockdata_c96`; by hand only |
| `proto_met_catchem_local` | `local` | C48, 6 PETs, `esm.C48.local.configure`: `grid_source: analytic` and the no-diagnostics science config, nothing read from or written to disk; never run by CI |

### Measured (2026-10-27)

The local-daemon rows ran under Rosetta emulation on this Mac; the first row ran natively on the cloud builder.

| Run | Result |
| --- | --- |
| `tools/ci.sh test ci` (the `test` stage on the cloud builder's amd64 node, native; three runs) | all 7 `ci` tests pass every time: `proto_met_catchem` 28.4-28.8 s, `proto_met_catchem_np12` 18.5-19.1 s, `make_mock_data` 6-7 s (fix files from the cache mount), the rest about a second; `ctest` total about 56 s. Mosaic grid `tileCount=6`, `regDecompPTile` 1x1 and 1x2, 43 of 43 exports reference-shared on every PET, global cell area / 4πR² = 1.0000, 144 aqmio diagnostic files per run |
| `proto_met_catchem_local` (C48, 6 PETs, analytic grid, no diagnostics) | 120 steps in 14 s wall; 43 of 43 exports shared; sea-salt bin 1 maximum grows from 0 to 1.1e-1 ug/kg over the day |
| the same config on 12 PETs | 120 steps in 14 s wall, `regDecompPTile=1x2` |
| C96 analytic, 12 PETs (`esm.C96.configure` edited to `grid_source: analytic` and the `_nodiag` science config) | 120 steps in 41 s wall, local DE 96x48 |
| `proto_met_catchem`, `proto_met_catchem_np12`, `proto_met_catchem_c96` in the local daemon on this Mac (Rosetta) | **not runnable**: `ESMF_GridCreateMosaic` opens the mosaic with netCDF and HDF5's initialization executes x87 `ffreep`, which Rosetta does not emulate (`SIGILL`, section 7); hence `tools/ci.sh test`. |

Link check (task 3): `nm -C build/protos/met_catchem/esmApp` shows 990 `catchem_yaml::` and 770 `YAML::` symbols, CATChem's vendored yaml-cpp and ESMF's embedded one in different namespaces, so the long-standing yaml-cpp reminder is closed for CATChem.
