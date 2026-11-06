"""The run manifest: one pydantic model tree describing everything `make_mock_data` produced.

The manifest is the contract later tasks read back: task 3's analytic MET component takes `met`, task 5's
config generator takes the grid, clock, categories and regrid mapping, tasks 4 and 9 take the per-category
files and `totals.yaml`. Every model forbids unknown keys and every field carries a description, so a
hand-edited manifest fails loudly instead of being half-read. Every generated field is a closed-form function
of the parameters here (no random numbers), so a run is reproducible from the manifest alone. The default
parameter set is the YAML file `tools/defaults/manifest.yaml` (`ManifestDefaults`), not Python literals.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from tools.settings import get_settings

ResolutionName = Literal["C48", "C96"]
Cadence = Literal["hourly", "daily", "monthly", "static"]
RegridMethod = Literal["conserve", "bilinear", "neareststod", "none"]
CeceMapalgo = Literal["consd", "bilinear", "nn", "passthrough"]
LevelOrder = Literal["bottom_up", "top_down"]
MaskedBy = Literal["land", "ocean", "none"]
FileFormat = Literal["netcdf", "point_table"]
NetcdfModel = Literal[
    "NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_CLASSIC", "NETCDF3_64BIT_OFFSET"
]
DataType = Literal["float32", "float64"]

# Code enumerations, not configuration: the analytic profile's categories (`tools/defaults/manifest.yaml`;
# the realistic profile names CATChem's production categories) and the CATChem -> CECE regrid mapping.
CATEGORY_NAMES: tuple[str, ...] = ("anthro", "fire", "gmi", "dms", "dust", "fengsha")
REGRID_TO_MAPALGO: dict[str, CeceMapalgo] = {
    "conserve": "consd",
    "bilinear": "bilinear",
    "neareststod": "nn",
    "none": "passthrough",
}
DATE_TOKENS = ("%y4", "%m2", "%d2")


class Strict(BaseModel):
    """Base for every manifest model: unknown keys are an error; every model reads and writes itself as YAML."""

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        return cls.model_validate(yaml.safe_load(path.read_text()))

    def to_yaml(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False))


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
        description="Number of latitude cells; the outer cell bounds must stay within ±90 (pole_centred grids may overshoot by half a cell, which the bounds clip).",
        gt=0,
    )
    pole_centred: bool = Field(
        default=False,
        description="True for grids whose first and last centres sit on the poles (181 latitudes at 1°): the polar cells are half cells and their outer bounds are clipped to ±90.",
    )

    @model_validator(mode="after")
    def _covers_the_globe(self) -> LatLonGrid:
        if abs(self.nlon * self.dlon - 360.0) > 1e-9:
            raise ValueError(f"nlon * dlon must equal 360, got {self.nlon * self.dlon}")
        south = self.lat_first - self.dlat / 2
        north = self.lat_first + (self.nlat - 1) * self.dlat + self.dlat / 2
        slack = self.dlat / 2 if self.pole_centred else 0.0
        if south < -90.0 - slack - 1e-9 or north > 90.0 + slack + 1e-9:
            raise ValueError(
                f"latitude bounds must stay within ±90, got [{south}, {north}]"
            )
        return self

    def coarsened(self, factor: int) -> LatLonGrid:
        """The grid with `factor` times the spacing; `factor` must divide the cell counts (the interior count for a pole_centred grid)."""
        if factor < 1:
            raise ValueError("grid factor must be at least 1")
        if factor == 1:
            return self
        interior = self.nlat - 1 if self.pole_centred else self.nlat
        if self.nlon % factor or interior % factor:
            raise ValueError(
                f"grid factor {factor} does not divide the grid ({self.nlon} x {self.nlat}{', pole-centred' if self.pole_centred else ''})"
            )
        shift = 0.0 if self.pole_centred else (factor - 1) / 2
        return LatLonGrid(
            dlon=self.dlon * factor,
            dlat=self.dlat * factor,
            lon_first=self.lon_first + shift * self.dlon,
            lat_first=self.lat_first + shift * self.dlat,
            nlon=self.nlon // factor,
            nlat=interior // factor + (1 if self.pole_centred else 0),
            pole_centred=self.pole_centred,
        )


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


class ExplicitTimeAxis(Strict):
    """A file's own time coordinate, written verbatim instead of the analytic axis (monthly and static files of the realistic profile)."""

    units: str = Field(
        description="CF time units string of the file, e.g. 'days since 2010-01-01 00:00:00'."
    )
    calendar: str = Field(default="standard", description="CF calendar attribute.")
    values: list[float] = Field(
        description="Every record time in `units`, in file order.", min_length=1
    )


class FileLayout(Strict):
    """How a category's file is laid out on disk when it mirrors a real inventory (the realistic profile); absent for the analytic profile's CF layout."""

    format: FileFormat = Field(
        default="netcdf",
        description="netcdf for a gridded inventory; point_table for a volcanic .rc table copied verbatim (no variables, nothing generated).",
    )
    model: NetcdfModel = Field(
        default="NETCDF4",
        description="NetCDF data model to write (netCDF4 format names).",
    )
    lon_name: str = Field(
        default="lon", description="Longitude coordinate variable name."
    )
    lat_name: str = Field(
        default="lat", description="Latitude coordinate variable name."
    )
    lev_name: str = Field(
        default="lev", description="Level coordinate variable name (3-D files)."
    )
    time_name: str = Field(default="time", description="Time coordinate variable name.")
    dtype: DataType = Field(default="float32", description="Data variable type.")
    fill_value: float | None = Field(
        default=None,
        description="_FillValue written on every data variable, or null for none.",
    )
    lev_before_time: bool = Field(
        default=False,
        description="True when the file orders dimensions (lev, time, lat, lon) instead of (time, lev, lat, lon).",
    )
    write_bounds_and_area: bool = Field(
        default=True,
        description="Write lon/lat bounds, time_bnds and the area variable (the analytic layout); the real inventories carry none.",
    )
    daily_record_hour: float = Field(
        default=12.0,
        description="Hour of the single record of a daily file (hours since that day's 00:00 UTC).",
        ge=0,
        lt=24,
    )
    time_axis: ExplicitTimeAxis | None = Field(
        default=None,
        description="The file's own time coordinate for monthly and static files, or null for the analytic axis; not allowed for hourly and daily files.",
    )


class Category(Strict):
    file: Path = Field(
        description="NetCDF file holding this category's variables; a daily category's path carries the %y4%m2%d2 tokens of its day."
    )
    cadence: Cadence = Field(
        description="CATChem's `frequency` for the category: hourly, daily, monthly or static."
    )
    regrid_method: RegridMethod = Field(
        description="CATChem-style regrid method the prototype assigns to the category."
    )
    cece_mapalgo: CeceMapalgo | None = Field(
        default=None,
        description="CECE stream mapalgo; derived from regrid_method when omitted (conserve→consd, bilinear→bilinear, neareststod→nn, none→passthrough).",
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
        description="Variables by their CATChem file variable name (empty only for a point_table layout)."
    )
    layout: FileLayout | None = Field(
        default=None,
        description="Real-inventory file layout (the realistic profile), or null for the analytic CF layout.",
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

    @model_validator(mode="after")
    def _layout_is_consistent(self) -> Category:
        layout = self.layout
        if layout is not None and layout.format == "point_table":
            if self.variables:
                raise ValueError("a point_table category has no variables")
            if self.is_3d:
                raise ValueError("a point_table category is not 3-D")
        elif not self.variables:
            raise ValueError("a gridded category needs at least one variable")
        if (
            layout is not None
            and layout.time_axis is not None
            and self.cadence in ("hourly", "daily")
        ):
            raise ValueError(
                f"an explicit time_axis is not allowed for a {self.cadence} category"
            )
        has_tokens = any(token in str(self.file) for token in DATE_TOKENS)
        if (self.cadence == "daily") != has_tokens:
            raise ValueError(
                "a daily category's file carries %y4%m2%d2 date tokens, other cadences do not"
            )
        return self

    @property
    def is_point_table(self) -> bool:
        return self.layout is not None and self.layout.format == "point_table"

    def files_for(self, clock: Clock) -> list[Path]:
        """The files of the category for the clock: one per calendar day the run touches for daily, else the one file."""
        if self.cadence != "daily":
            return [self.file]
        first = clock.start.astimezone(UTC).date()
        last = (clock.end - timedelta(microseconds=1)).astimezone(UTC).date()
        days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
        return [expand_date_tokens(self.file, day) for day in days]


def expand_date_tokens(path: Path, day: date) -> Path:
    """`%y4`, `%m2`, `%d2` (the CATChem, ExtData and HEMCO spelling) replaced by the day's year, month and day."""
    text = str(path)
    for token, value in zip(
        DATE_TOKENS,
        (f"{day.year:04d}", f"{day.month:02d}", f"{day.day:02d}"),
        strict=True,
    ):
        text = text.replace(token, value)
    return Path(text)


class Tracer(Strict):
    name: str = Field(
        description="Host tracer name written to the tracerNames metadata of the rank-4 tracer field; CATChem matches it case-insensitively against its species.",
        min_length=1,
    )
    units: str = Field(
        description="Units string written to tracerUnits (kg/kg for sphum, ug/kg for aerosols, ppm for gases); CATChem derives its conversion factor from it.",
        min_length=1,
    )


class MetParams(Strict):
    """Parameters of the analytic meteorology the MET component (protos/met_catchem/proto_met.F90) computes at run time; nothing is written to disk."""

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
    n_soil_layers: int = Field(
        description="Number of soil layers of the soil moisture and soil temperature fields (Noah: 4).",
        gt=0,
    )
    pressure_top_pa: float = Field(
        description="Pressure at the model top, Pa; the column is quadratic in sigma between it and the surface pressure.",
        gt=0,
    )
    orography_max_m: float = Field(
        description="Peak surface height of the analytic orography (land fraction times cos^2 latitude), m.",
        ge=0,
    )
    specific_humidity_surface: float = Field(
        description="Specific humidity at the surface, kg/kg; decays as sigma^2 with height and sets sphum and QV2M.",
        ge=0,
        lt=1,
    )
    cloud_fraction_max: float = Field(
        description="Peak of the analytic cloud fraction profile, 0-1.", ge=0, le=1
    )
    friction_velocity_land_m_s: float = Field(
        description="Friction velocity over land, m/s.", ge=0
    )
    friction_velocity_ocean_m_s: float = Field(
        description="Friction velocity over ocean, m/s.", ge=0
    )
    roughness_length_land_cm: float = Field(
        description="Surface roughness length over land, cm (the CATChem cap multiplies inst_surface_roughness by 0.01).",
        gt=0,
    )
    roughness_length_ocean_cm: float = Field(
        description="Surface roughness length over ocean, cm.", gt=0
    )
    shortwave_peak_w_m2: float = Field(
        description="Downward shortwave flux at the surface for an overhead sun, W/m2; scaled by the cosine of the solar zenith angle.",
        ge=0,
    )
    sensible_heat_peak_w_m2: float = Field(
        description="Sensible heat flux over land for an overhead sun, W/m2.", ge=0
    )
    latent_heat_peak_w_m2: float = Field(
        description="Latent heat flux for an overhead sun, W/m2.", ge=0
    )
    aerodynamic_conductance_s_m: float = Field(
        description="Aerodynamic conductance exported as inst_aerodynamic_conductance, s/m.",
        gt=0,
    )
    canopy_resistance_s_m: float = Field(
        description="Canopy resistance exported as inst_canopy_resistance, s/m.", gt=0
    )
    leaf_area_index: float = Field(
        description="Leaf area index over land, m2/m2.", ge=0
    )
    vegetation_fraction: float = Field(
        description="Green vegetation fraction over land, 0-1.", ge=0, le=1
    )
    sea_ice_latitude_deg: float = Field(
        description="Ocean cells poleward of this latitude (degrees) are fully sea-ice covered.",
        ge=0,
        le=90,
    )
    snow_latitude_deg: float = Field(
        description="Land cells poleward of this latitude (degrees) are fully snow covered.",
        ge=0,
        le=90,
    )
    soil_type_class: int = Field(
        description="Soil type class index reported over land cells.", ge=0
    )
    tracers: list[Tracer] = Field(
        description="The host tracers of the rank-4 inst_tracer_mass_frac field, in slot order; sphum must be present exactly once.",
        min_length=1,
    )

    @model_validator(mode="after")
    def _tracers_and_column_are_consistent(self) -> MetParams:
        names = [t.name.lower() for t in self.tracers]
        if len(set(names)) != len(names):
            raise ValueError("tracer names must be unique (case-insensitive)")
        if names.count("sphum") != 1:
            raise ValueError("tracers must contain sphum exactly once")
        if self.pressure_top_pa >= self.surface_pressure_pa:
            raise ValueError("pressure_top_pa must be below surface_pressure_pa")
        return self


class Parameters(Strict):
    """The parameter set shared by the defaults file and the manifest: everything but who/what/where."""

    earth_radius_m: float = Field(
        description="Earth radius used for the inventory cell areas, m (FV3's RADIUS).",
        gt=0,
    )
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
    def _references_resolve(self) -> Self:
        for name, category in self.categories.items():
            if category.grid not in self.grids:
                raise ValueError(
                    f"category {name!r} references unknown grid {category.grid!r}"
                )
        if self.land_mask.grid not in self.grids:
            raise ValueError(
                f"land_mask references unknown grid {self.land_mask.grid!r}"
            )
        by_file: dict[Path, tuple[str, Category]] = {}
        for name, category in self.categories.items():
            first = by_file.setdefault(category.file, (name, category))
            if first[1] is category:
                continue
            other = first[1]
            agree = (
                other.grid == category.grid
                and other.cadence == category.cadence
                and other.is_3d == category.is_3d
                and other.level_order == category.level_order
                and other.layout == category.layout
            )
            if not agree:
                raise ValueError(
                    f"categories {first[0]!r} and {name!r} share the file {category.file} but disagree on grid, cadence, levels or layout"
                )
            if set(other.variables) & set(category.variables):
                raise ValueError(
                    f"categories {first[0]!r} and {name!r} share the file {category.file} and a variable name"
                )
        return self

    def categories_by_file(self) -> dict[Path, list[str]]:
        """Category names grouped by the file they are written to, in manifest order."""
        groups: dict[Path, list[str]] = {}
        for name, category in self.categories.items():
            groups.setdefault(category.file, []).append(name)
        return groups

    @property
    def inventories_dir(self) -> Path:
        return self.land_mask.file.parent


class ManifestDefaults(Parameters):
    """The checked-in defaults (`tools/defaults/manifest.yaml`): file paths are relative to the output directory."""


class Manifest(Parameters):
    generator: GeneratorInfo = Field(description="Who wrote the manifest and how.")
    resolution: ResolutionName = Field(
        description="Cubed-sphere resolution: C48 (CI) or C96 (prototype default)."
    )
    fix: FixFiles = Field(description="The FV3 fix files for the resolution.")

    @property
    def n(self) -> int:
        return int(self.resolution[1:])


# --- building a manifest from the defaults file ------------------------------------------------------------


def inventory_grid(resolution_deg: float) -> LatLonGrid:
    """A global cell-centred grid with the given spacing (first centre at -180 + d/2, -90 + d/2)."""
    return LatLonGrid(
        dlon=resolution_deg,
        dlat=resolution_deg,
        lon_first=-180.0 + resolution_deg / 2,
        lat_first=-90.0 + resolution_deg / 2,
        nlon=round(360.0 / resolution_deg),
        nlat=round(180.0 / resolution_deg),
    )


def _rooted(path: Path, out_dir: Path) -> Path:
    return path if path.is_absolute() else out_dir / path


def default_manifest(
    *,
    resolution: ResolutionName,
    out_dir: Path,
    command: str,
    defaults_file: Path | None = None,
    start: datetime | None = None,
    duration_hours: int | None = None,
    step_seconds: int | None = None,
    n_levels: int | None = None,
    inventory_resolution_deg: float | None = None,
    source_url: str | None = None,
    generated_at: datetime | None = None,
    grid_factor: int = 1,
) -> Manifest:
    """The defaults file (settings `defaults_file` unless given) with the overrides applied, every grid coarsened by `grid_factor`, and every path rooted under `out_dir`."""
    settings = get_settings()
    defaults = ManifestDefaults.from_yaml(defaults_file or settings.defaults_file)
    overrides = {
        "start": start,
        "duration_hours": duration_hours,
        "step_seconds": step_seconds,
        "n_levels": n_levels,
    }
    clock = Clock.model_validate(
        defaults.clock.model_dump()
        | {k: v for k, v in overrides.items() if v is not None}
    )
    grids = dict(defaults.grids)
    if inventory_resolution_deg is not None:
        grids["inventory"] = inventory_grid(inventory_resolution_deg)
    grids = {name: grid.coarsened(grid_factor) for name, grid in grids.items()}
    fix_dir = out_dir / settings.fix_dirname
    return Manifest(
        generator=GeneratorInfo(
            version=settings.generator_version,
            generated_at=generated_at or datetime.now(tz=UTC),
            command=command,
        ),
        resolution=resolution,
        earth_radius_m=defaults.earth_radius_m,
        fix=FixFiles(
            source_url=(source_url or settings.fix_url).format(resolution=resolution),
            directory=fix_dir,
            mosaic=fix_dir / f"{resolution}_mosaic.nc",
            grid_tiles=[fix_dir / f"{resolution}_grid.tile{t}.nc" for t in range(1, 7)],
            grid_spec_tiles=[
                fix_dir / f"{resolution}_grid_spec.tile{t}.nc" for t in range(1, 7)
            ],
        ),
        clock=clock,
        grids=grids,
        land_mask=defaults.land_mask.model_copy(
            update={"file": _rooted(defaults.land_mask.file, out_dir)}
        ),
        categories={
            name: c.model_copy(update={"file": _rooted(c.file, out_dir)})
            for name, c in defaults.categories.items()
        },
        met=defaults.met,
    )
