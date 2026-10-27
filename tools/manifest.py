"""The run manifest: one pydantic model tree describing everything `make_mock_data` produced.

The manifest is the contract later tasks read back: task 3's analytic MET component takes `met`, task 5's
config generator takes the grid, clock, categories and regrid mapping, tasks 4 and 9 take the per-category
files and `totals.yaml`. Every model forbids unknown keys and every field carries a description, so a
hand-edited manifest fails loudly instead of being half-read. Every generated field is a closed-form function
of the parameters here (no random numbers), so a run is reproducible from the manifest alone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

ResolutionName = Literal["C48", "C96"]
Cadence = Literal["hourly", "monthly", "static"]
RegridMethod = Literal["conserve", "bilinear", "none"]
CeceMapalgo = Literal["consd", "bilinear", "passthrough"]
LevelOrder = Literal["bottom_up", "top_down"]
MaskedBy = Literal["land", "ocean", "none"]

CATEGORY_NAMES: tuple[str, ...] = ("anthro", "fire", "gmi", "dms", "dust", "fengsha")
REGRID_TO_MAPALGO: dict[str, CeceMapalgo] = {
    "conserve": "consd",
    "bilinear": "bilinear",
    "none": "passthrough",
}
EMC_FIX_URL = "https://ftp.emc.ncep.noaa.gov/static_files/public/UFS/GFS/fix/fix_fv3/{resolution}/"
EARTH_RADIUS_M = 6371.0e3
MANIFEST_FILENAME = "manifest.yaml"
TOTALS_FILENAME = "totals.yaml"
FIX_DIRNAME = "fix"
INVENTORIES_DIRNAME = "inventories"
LANDMASK_FILENAME = "landmask.nc"
GENERATOR_VERSION = "0.1.0"
DEFAULT_START = datetime(
    2021, 3, 22, 6, tzinfo=UTC
)  # catchem_control: SYEAR/SMONTH/SDAY/SHOUR


class Strict(BaseModel):
    """Base for every manifest model: unknown keys are an error."""

    model_config = ConfigDict(extra="forbid")


class GeneratorInfo(Strict):
    version: str = Field(
        description="Version of the mock data generator that wrote this manifest."
    )
    generated_at: AwareDatetime = Field(
        description="UTC time at which the manifest was written."
    )
    command: str = Field(
        description="The command line that produced this run, for the record."
    )


class FixFiles(Strict):
    source_url: str = Field(
        description="Directory URL the fix files were (or would be) downloaded from."
    )
    directory: Path = Field(
        description="Local directory holding the mosaic and the twelve tile files."
    )
    mosaic: Path = Field(
        description="Path of the GRIDSPEC mosaic file (<res>_mosaic.nc)."
    )
    grid_tiles: list[Path] = Field(
        description="The six supergrid files <res>_grid.tileN.nc, tile 1 first.",
        min_length=6,
        max_length=6,
    )
    grid_spec_tiles: list[Path] = Field(
        description="The six <res>_grid_spec.tileN.nc files with cell centres and areas, tile 1 first.",
        min_length=6,
        max_length=6,
    )
    sizes: dict[str, int] = Field(
        default_factory=dict,
        description="Byte size of each fix file by file name, recorded after validation.",
    )


class Clock(Strict):
    start: AwareDatetime = Field(
        description="Run start (timezone-aware, UTC); the first record of every hourly and static inventory."
    )
    duration_hours: int = Field(
        description="Run length in hours; hourly inventories cover it inclusively.",
        gt=0,
    )
    step_seconds: int = Field(
        description="Coupling step in seconds (DT_ATMOS and coupling_interval_sec in catchem_control).",
        gt=0,
    )
    n_levels: int = Field(
        description="Number of model levels (NPZ); the vertical size of 3-D inventories.",
        gt=0,
    )

    @model_validator(mode="after")
    def _step_divides_duration(self) -> Clock:
        if (self.duration_hours * 3600) % self.step_seconds:
            raise ValueError(
                "step_seconds must divide the duration (duration_hours * 3600)"
            )
        return self

    @property
    def end(self) -> datetime:
        return self.start + timedelta(hours=self.duration_hours)

    @property
    def n_steps(self) -> int:
        return self.duration_hours * 3600 // self.step_seconds


class LatLonGrid(Strict):
    dlon: float = Field(description="Longitude spacing in degrees.", gt=0)
    dlat: float = Field(description="Latitude spacing in degrees.", gt=0)
    lon_first: float = Field(
        description="Longitude of the first cell centre in degrees east."
    )
    lat_first: float = Field(
        description="Latitude of the first (southernmost) cell centre in degrees north."
    )
    nlon: int = Field(
        description="Number of longitude cells; nlon * dlon must be 360.", gt=0
    )
    nlat: int = Field(
        description="Number of latitude cells; the outer cell bounds must stay within ±90.",
        gt=0,
    )

    @model_validator(mode="after")
    def _covers_the_globe(self) -> LatLonGrid:
        if abs(self.nlon * self.dlon - 360.0) > 1e-9:
            raise ValueError(f"nlon * dlon must equal 360, got {self.nlon * self.dlon}")
        south = self.lat_first - self.dlat / 2
        north = self.lat_first + (self.nlat - 1) * self.dlat + self.dlat / 2
        if south < -90.0 - 1e-9 or north > 90.0 + 1e-9:
            raise ValueError(
                f"latitude bounds must stay within ±90, got [{south}, {north}]"
            )
        return self


class Continent(Strict):
    lon: float = Field(
        description="Centre longitude of the land shape in degrees east."
    )
    lat: float = Field(
        description="Centre latitude of the land shape in degrees north."
    )
    semi_axis_lon: float = Field(
        description="Half-width of the shape along its rotated x axis, in degrees.",
        gt=0,
    )
    semi_axis_lat: float = Field(
        description="Half-height of the shape along its rotated y axis, in degrees.",
        gt=0,
    )
    rotation_deg: float = Field(
        default=0.0,
        description="Counter-clockwise rotation of the shape's axes in degrees.",
    )
    exponent: float = Field(
        default=4.0,
        description="Super-Gaussian exponent; larger values give flatter tops and sharper edges.",
        gt=0,
    )


class LandMask(Strict):
    grid: str = Field(
        description="Name (in `grids`) of the grid the mask is written on."
    )
    file: Path = Field(description="NetCDF file holding land_fraction and land_mask.")
    threshold: float = Field(
        default=0.5, description="land_mask = land_fraction >= threshold.", gt=0, lt=1
    )
    continents: list[Continent] = Field(
        description="Super-Gaussian land shapes whose clipped sum is the land fraction."
    )


class GaussianSource(Strict):
    lon: float = Field(description="Centre longitude of the source in degrees east.")
    lat: float = Field(description="Centre latitude of the source in degrees north.")
    sigma_deg: float = Field(
        description="Standard deviation of the Gaussian in degrees (isotropic in lon/lat).",
        gt=0,
    )
    amplitude: float = Field(
        description="Peak value of the source in the variable's units before masking and cycles.",
        ge=0,
    )


class Diurnal(Strict):
    relative_amplitude: float = Field(
        description="Relative amplitude d of the factor 1 + d cos(2 pi (t_local - peak) / 24 h).",
        ge=0,
        le=1,
    )
    peak_local_hour: float = Field(
        description="Local solar hour of the daily maximum.", ge=0, lt=24
    )


class Seasonal(Strict):
    relative_amplitude: float = Field(
        description="Relative amplitude s of the factor 1 + s cos(2 pi (month - peak) / 12).",
        ge=0,
        le=1,
    )
    peak_month: int = Field(
        description="Calendar month (1-12) of the annual maximum.", ge=1, le=12
    )


class Vertical(Strict):
    scale_height_levels: float = Field(
        description="e-folding depth in level units of exp(-k / H), k = 0 at the surface.",
        gt=0,
    )


class Variable(Strict):
    units: str = Field(
        description="CF units string written to the variable (informational: consumers do not convert)."
    )
    long_name: str = Field(description="CF long_name written to the variable.")
    background: float = Field(
        description="Value added everywhere (inside the mask) before the Gaussian sources.",
        ge=0,
    )
    sources: list[GaussianSource] = Field(
        description="Gaussian sources summed onto the background."
    )
    diurnal: Diurnal | None = Field(
        default=None, description="Diurnal cycle applied to hourly data, or null."
    )
    seasonal: Seasonal | None = Field(
        default=None, description="Seasonal cycle applied to monthly data, or null."
    )
    vertical: Vertical | None = Field(
        default=None, description="Vertical profile for 3-D data, or null."
    )


class Category(Strict):
    file: Path = Field(description="NetCDF file holding this category's variables.")
    cadence: Cadence = Field(
        description="CATChem's `frequency` for the category: hourly, monthly or static."
    )
    regrid_method: RegridMethod = Field(
        description="CATChem-style regrid method the prototype assigns to the category."
    )
    cece_mapalgo: CeceMapalgo | None = Field(
        default=None,
        description="CECE stream mapalgo; derived from regrid_method when omitted (conserve→consd, bilinear→bilinear, none→passthrough).",
    )
    grid: str = Field(
        description="Name (in `grids`) of the regular lat-lon grid the file is written on."
    )
    is_3d: bool = Field(
        description="True when the variables carry a `lev` dimension of n_levels."
    )
    level_order: LevelOrder = Field(
        default="bottom_up",
        description="Ordering of the `lev` dimension: bottom_up (index 0 at the surface) or top_down.",
    )
    masked_by: MaskedBy = Field(
        description="Multiply by the land fraction, the ocean fraction, or nothing."
    )
    variables: dict[str, Variable] = Field(
        description="Variables by their CATChem file variable name."
    )

    @model_validator(mode="after")
    def _mapalgo_matches_regrid_method(self) -> Category:
        expected = REGRID_TO_MAPALGO[self.regrid_method]
        if self.cece_mapalgo is None:
            self.cece_mapalgo = expected
        elif self.cece_mapalgo != expected:
            raise ValueError(
                f"cece_mapalgo {self.cece_mapalgo!r} does not match regrid_method {self.regrid_method!r} (expected {expected!r})"
            )
        return self


class MetParams(Strict):
    """Parameters of the analytic meteorology task 3's MET component computes at run time (nothing is written to disk)."""

    surface_pressure_pa: float = Field(
        description="Mean sea-level pressure used for the surface pressure field, Pa.",
        gt=0,
    )
    temperature_equator_k: float = Field(
        description="Surface air temperature at the equator, K.", gt=0
    )
    temperature_pole_k: float = Field(
        description="Surface air temperature at the poles, K.", gt=0
    )
    lapse_rate_k_per_level: float = Field(
        description="Temperature decrease per model level below the tropopause, K.",
        ge=0,
    )
    tropopause_level: int = Field(
        description="Level index (0 at the surface) above which temperature is held constant.",
        ge=0,
    )
    zonal_wind_amplitude_m_s: float = Field(
        description="Peak zonal wind of the analytic jet, m/s.", ge=0
    )
    pbl_height_m: float = Field(description="Boundary-layer height, m.", gt=0)
    soil_moisture: float = Field(
        description="Volumetric soil moisture, fraction.", ge=0, le=1
    )
    land_use_class: int = Field(
        description="Land-use class index reported over land cells.", ge=0
    )
    diurnal_amplitude_k: float = Field(
        description="Amplitude of the surface temperature diurnal cycle, K.", ge=0
    )
    diurnal_peak_local_hour: float = Field(
        description="Local solar hour of the temperature maximum.", ge=0, lt=24
    )


class Manifest(Strict):
    generator: GeneratorInfo = Field(description="Who wrote the manifest and how.")
    resolution: ResolutionName = Field(
        description="Cubed-sphere resolution: C48 (CI) or C96 (prototype default)."
    )
    earth_radius_m: float = Field(
        default=EARTH_RADIUS_M,
        description="Earth radius used for the inventory cell areas, m (FV3's RADIUS).",
        gt=0,
    )
    fix: FixFiles = Field(description="The FV3 fix files for the resolution.")
    clock: Clock = Field(description="Start, length, step and levels of the run.")
    grids: dict[str, LatLonGrid] = Field(
        description="Named regular lat-lon grids the inventories are written on."
    )
    land_mask: LandMask = Field(description="The analytic land mask.")
    categories: dict[str, Category] = Field(
        description="One entry per CATChem emission category."
    )
    met: MetParams = Field(
        description="Analytic meteorology parameters for task 3's MET component."
    )

    @model_validator(mode="after")
    def _references_resolve(self) -> Manifest:
        for name in self.categories:
            if name not in CATEGORY_NAMES:
                raise ValueError(
                    f"unknown category {name!r}; expected one of {', '.join(CATEGORY_NAMES)}"
                )
        for name, category in self.categories.items():
            if category.grid not in self.grids:
                raise ValueError(
                    f"category {name!r} references unknown grid {category.grid!r}"
                )
        if self.land_mask.grid not in self.grids:
            raise ValueError(
                f"land_mask references unknown grid {self.land_mask.grid!r}"
            )
        return self

    @property
    def n(self) -> int:
        return int(self.resolution[1:])

    @property
    def inventories_dir(self) -> Path:
        return self.land_mask.file.parent


def write_manifest(manifest: Manifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(manifest.model_dump(mode="json"), sort_keys=False))


def read_manifest(path: Path) -> Manifest:
    return Manifest.model_validate(yaml.safe_load(path.read_text()))


# --- defaults ----------------------------------------------------------------------------------------------


def _grid(resolution_deg: float) -> LatLonGrid:
    return LatLonGrid(
        dlon=resolution_deg,
        dlat=resolution_deg,
        lon_first=-180.0 + resolution_deg / 2,
        lat_first=-90.0 + resolution_deg / 2,
        nlon=round(360.0 / resolution_deg),
        nlat=round(180.0 / resolution_deg),
    )


def _src(lon: float, lat: float, sigma: float, amplitude: float) -> GaussianSource:
    return GaussianSource(lon=lon, lat=lat, sigma_deg=sigma, amplitude=amplitude)


# Three industrial/populated regions on the three default continents (Eurasia-Africa, the Americas, Australia).
_LAND_CENTRES = ((15.0, 48.0), (-80.0, 40.0), (115.0, 32.0))
_FIRE_CENTRES = ((25.0, -5.0), (-60.0, -10.0), (135.0, -20.0))


def _flux(
    long_name: str,
    scale: float,
    centres: tuple[tuple[float, float], ...],
    diurnal: Diurnal,
) -> Variable:
    sources = [
        _src(lon, lat, 12.0 + 3.0 * i, scale * (1.0 + 0.25 * i))
        for i, (lon, lat) in enumerate(centres)
    ]
    return Variable(
        units="kg m-2 s-1",
        long_name=long_name,
        background=0.0,
        sources=sources,
        diurnal=diurnal,
    )


def default_categories(inventories_dir: Path) -> dict[str, Category]:
    anthro_cycle = Diurnal(relative_amplitude=0.5, peak_local_hour=14.0)
    fire_cycle = Diurnal(relative_amplitude=0.8, peak_local_hour=15.0)
    summer = Seasonal(relative_amplitude=0.3, peak_month=7)
    winter = Seasonal(relative_amplitude=0.2, peak_month=1)
    column = Vertical(scale_height_levels=25.0)
    return {
        "anthro": Category(
            file=inventories_dir / "anthro.nc",
            cadence="hourly",
            regrid_method="conserve",
            grid="inventory",
            is_3d=False,
            masked_by="land",
            variables={
                "NO": _flux(
                    "Nitrogen oxide emission flux", 2.0e-10, _LAND_CENTRES, anthro_cycle
                ),
                "SO2": _flux(
                    "Sulfur dioxide emission flux", 1.5e-10, _LAND_CENTRES, anthro_cycle
                ),
                "POC": _flux(
                    "Primary organic carbon emission flux",
                    4.0e-11,
                    _LAND_CENTRES,
                    anthro_cycle,
                ),
                "TERP": _flux(
                    "Terpene emission flux", 3.0e-11, _LAND_CENTRES, anthro_cycle
                ),
                "ISOP": _flux(
                    "Isoprene emission flux", 5.0e-11, _LAND_CENTRES, anthro_cycle
                ),
                "PEC": _flux(
                    "Primary elemental carbon emission flux",
                    2.0e-11,
                    _LAND_CENTRES,
                    anthro_cycle,
                ),
                "PSO4": _flux(
                    "Primary sulfate emission flux",
                    1.0e-11,
                    _LAND_CENTRES,
                    anthro_cycle,
                ),
            },
        ),
        "fire": Category(
            file=inventories_dir / "fire.nc",
            cadence="hourly",
            regrid_method="conserve",
            grid="inventory",
            is_3d=False,
            masked_by="land",
            variables={
                "NO": _flux(
                    "Fire nitrogen oxide emission flux",
                    1.0e-10,
                    _FIRE_CENTRES,
                    fire_cycle,
                ),
                "SO2": _flux(
                    "Fire sulfur dioxide emission flux",
                    3.0e-11,
                    _FIRE_CENTRES,
                    fire_cycle,
                ),
                "CO": _flux(
                    "Fire carbon monoxide emission flux",
                    2.0e-9,
                    _FIRE_CENTRES,
                    fire_cycle,
                ),
                "FRP": Variable(
                    units="MW",
                    long_name="Fire radiative power",
                    background=0.0,
                    sources=[_src(lon, lat, 10.0, 50.0) for lon, lat in _FIRE_CENTRES],
                    diurnal=fire_cycle,
                ),
            },
        ),
        "gmi": Category(
            file=inventories_dir / "gmi.nc",
            cadence="monthly",
            regrid_method="bilinear",
            grid="gmi",
            is_3d=True,
            masked_by="none",
            variables={
                "oh": Variable(
                    units="cm-3",
                    long_name="Hydroxyl radical number concentration",
                    background=5.0e5,
                    sources=[_src(0.0, 0.0, 30.0, 2.0e6)],
                    seasonal=summer,
                    vertical=column,
                ),
                "h2o2": Variable(
                    units="mol mol-1",
                    long_name="Hydrogen peroxide volume mixing ratio",
                    background=2.0e-10,
                    sources=[_src(0.0, 10.0, 35.0, 1.5e-9)],
                    seasonal=summer,
                    vertical=column,
                ),
                "no3": Variable(
                    units="mol mol-1",
                    long_name="Nitrate radical volume mixing ratio",
                    background=1.0e-12,
                    sources=[_src(20.0, 40.0, 25.0, 2.0e-11)],
                    seasonal=winter,
                    vertical=column,
                ),
            },
        ),
        "dms": Category(
            file=inventories_dir / "dms.nc",
            cadence="monthly",
            regrid_method="bilinear",
            grid="inventory",
            is_3d=False,
            masked_by="ocean",
            variables={
                "dms": Variable(
                    units="nmol L-1",
                    long_name="Sea surface dimethyl sulfide concentration",
                    background=1.5,
                    sources=[
                        _src(-30.0, 50.0, 20.0, 4.0),
                        _src(160.0, -45.0, 25.0, 3.0),
                    ],
                    seasonal=summer,
                ),
            },
        ),
        "dust": Category(
            file=inventories_dir / "dust.nc",
            cadence="static",
            regrid_method="bilinear",
            grid="inventory",
            is_3d=False,
            masked_by="land",
            variables={
                "clayfrac": Variable(
                    units="1",
                    long_name="Clay fraction",
                    background=0.15,
                    sources=[_src(10.0, 22.0, 15.0, 0.25)],
                ),
                "sandfrac": Variable(
                    units="1",
                    long_name="Sand fraction",
                    background=0.30,
                    sources=[_src(10.0, 22.0, 15.0, 0.45), _src(45.0, 25.0, 10.0, 0.3)],
                ),
                "sep": Variable(
                    units="1",
                    long_name="Sediment supply map",
                    background=0.05,
                    sources=[_src(10.0, 22.0, 12.0, 0.9), _src(100.0, 40.0, 10.0, 0.7)],
                ),
                "uthres": Variable(
                    units="m s-1",
                    long_name="Threshold friction velocity",
                    background=0.35,
                    sources=[_src(10.0, 22.0, 15.0, 0.2)],
                ),
            },
        ),
        "fengsha": Category(
            file=inventories_dir / "fengsha.nc",
            cadence="monthly",
            regrid_method="bilinear",
            grid="inventory",
            is_3d=False,
            masked_by="land",
            variables={
                "albedo_drag": Variable(
                    units="1",
                    long_name="Drag partition",
                    background=0.2,
                    sources=[_src(10.0, 22.0, 15.0, 0.5)],
                    seasonal=winter,
                ),
            },
        ),
    }


def default_land_mask(inventories_dir: Path) -> LandMask:
    return LandMask(
        grid="inventory",
        file=inventories_dir / LANDMASK_FILENAME,
        continents=[
            Continent(
                lon=40.0,
                lat=25.0,
                semi_axis_lon=70.0,
                semi_axis_lat=40.0,
                rotation_deg=10.0,
            ),
            Continent(
                lon=-80.0,
                lat=5.0,
                semi_axis_lon=30.0,
                semi_axis_lat=60.0,
                rotation_deg=-20.0,
            ),
            Continent(lon=135.0, lat=-25.0, semi_axis_lon=18.0, semi_axis_lat=12.0),
        ],
    )


def default_met() -> MetParams:
    return MetParams(
        surface_pressure_pa=101325.0,
        temperature_equator_k=300.0,
        temperature_pole_k=250.0,
        lapse_rate_k_per_level=0.6,
        tropopause_level=90,
        zonal_wind_amplitude_m_s=25.0,
        pbl_height_m=800.0,
        soil_moisture=0.25,
        land_use_class=1,
        diurnal_amplitude_k=5.0,
        diurnal_peak_local_hour=14.0,
    )


def default_manifest(
    *,
    resolution: ResolutionName,
    out_dir: Path,
    command: str,
    start: datetime = DEFAULT_START,
    duration_hours: int = 24,
    step_seconds: int = 720,
    n_levels: int = 127,
    inventory_resolution_deg: float = 1.0,
    source_url: str = EMC_FIX_URL,
    generated_at: datetime | None = None,
) -> Manifest:
    """The default parameter set for a resolution: catchem_control's clock, 1° inventories, 2.5 x 2 degree gmi."""
    fix_dir = out_dir / FIX_DIRNAME
    inv_dir = out_dir / INVENTORIES_DIRNAME
    return Manifest(
        generator=GeneratorInfo(
            version=GENERATOR_VERSION,
            generated_at=generated_at or datetime.now(tz=start.tzinfo),
            command=command,
        ),
        resolution=resolution,
        fix=FixFiles(
            source_url=source_url.format(resolution=resolution),
            directory=fix_dir,
            mosaic=fix_dir / f"{resolution}_mosaic.nc",
            grid_tiles=[fix_dir / f"{resolution}_grid.tile{t}.nc" for t in range(1, 7)],
            grid_spec_tiles=[
                fix_dir / f"{resolution}_grid_spec.tile{t}.nc" for t in range(1, 7)
            ],
        ),
        clock=Clock(
            start=start,
            duration_hours=duration_hours,
            step_seconds=step_seconds,
            n_levels=n_levels,
        ),
        grids={
            "inventory": _grid(inventory_resolution_deg),
            "gmi": LatLonGrid(
                dlon=2.5,
                dlat=2.0,
                lon_first=-178.75,
                lat_first=-89.0,
                nlon=144,
                nlat=90,
            ),
        },
        land_mask=default_land_mask(inv_dir),
        categories=default_categories(inv_dir),
        met=default_met(),
    )
