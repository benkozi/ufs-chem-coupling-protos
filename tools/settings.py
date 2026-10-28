"""The shared settings object: every environment variable, tunable constant and default-file path of the tooling.

Nothing else under `tools/` reads the environment or hard-codes a tunable. Values come from the environment
with the `PROTOS_` prefix (`PROTOS_LOG_LEVEL=DEBUG`), else from the defaults here; `get_settings()` is the
single accessor (cached; tests clear it after changing the environment). Large default parameter sets are not
settings: the manifest defaults live in the YAML file `defaults_file` names and are parsed by the manifest
models.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PROTOS_", extra="forbid", frozen=True, str_strip_whitespace=True
    )

    # logging
    log_level: LogLevel = Field(
        default="INFO",
        description="Level of the project logger (PROTOS_LOG_LEVEL); the CLI's --log-level wins when given.",
    )
    log_format: str = Field(
        default="%(asctime)s %(levelname)s %(name)s: %(message)s",
        description="logging.Formatter format of the single stderr handler.",
    )

    # fix files
    fix_url: str = Field(
        default="https://ftp.emc.ncep.noaa.gov/static_files/public/UFS/GFS/fix/fix_fv3/{resolution}/",
        description="Directory URL template of the FV3 fix files; {resolution} is C48 or C96.",
    )
    download_timeout_s: float = Field(
        default=120.0,
        description="Socket timeout of each fix-file download, seconds.",
        gt=0,
    )
    download_chunk_bytes: int = Field(
        default=1 << 20,
        description="Read size while streaming a download to its .part file.",
        gt=0,
    )
    area_tolerance: float = Field(
        default=0.01,
        description="Allowed relative deviation of a grid_spec tile's total area from a sixth of the sphere.",
        gt=0,
        lt=1,
    )

    # inventories
    earth_radius_m: float = Field(
        default=6371.0e3,
        description="Earth radius used for the inventory cell areas, m (FV3's RADIUS).",
        gt=0,
    )
    cf_conventions: str = Field(
        default="CF-1.8",
        description="Value of the Conventions attribute written to every NetCDF file.",
    )
    deflate_level: int = Field(
        default=1,
        description="zlib deflate level of the inventory variables (0 disables compression).",
        ge=0,
        le=9,
    )
    deflate_shuffle: bool = Field(
        default=True, description="Apply the HDF5 shuffle filter before deflate."
    )

    # manifest and layout
    generator_version: str = Field(
        default="0.1.0",
        description="Version string the generator writes into the manifest.",
    )
    defaults_file: Path = Field(
        default=Path("tools/defaults/manifest.yaml"),
        description="YAML file with the default manifest parameters (PROTOS_DEFAULTS); --defaults wins when given.",
        validation_alias="PROTOS_DEFAULTS",
    )
    manifest_filename: str = Field(
        default="manifest.yaml", description="Name of the manifest written under --out."
    )
    totals_filename: str = Field(
        default="totals.yaml",
        description="Name of the totals file written under the inventories directory.",
    )
    fix_dirname: str = Field(
        default="fix", description="Subdirectory of --out holding the fix files."
    )

    # conservation check (task 4)
    met_totals_filename: str = Field(
        default="met_import_totals.yaml",
        description="Name of the totals file MET writes in its run directory (protos/met_catchem/proto_met.F90 TOTALS_FILE).",
    )
    conservation_rtol: float = Field(
        default=2e-6,
        description="Default relative tolerance of check_conservation (--rtol wins): the C48 anthro/NO error measured in task 4 (1.529e-6) rounded up.",
        gt=0,
    )
    conservation_atol: float = Field(
        default=1e-12,
        description="Absolute tolerance of check_conservation for records whose expected total is zero (--atol wins).",
        ge=0,
    )
    conservation_min_matches: int = Field(
        default=24,
        description="Smallest number of common time stamps check_conservation accepts (--min-matches wins): the hourly records inside a 24 h run.",
        ge=1,
    )

    # config generation (task 5). Relative paths are relative to the repository root.
    gen_defaults_file: Path = Field(
        default=Path("tools/defaults/gen_configs.yaml"),
        description="YAML file with the config generator's parameter set (PROTOS_GEN_DEFAULTS); --defaults wins when given.",
        validation_alias="PROTOS_GEN_DEFAULTS",
    )
    catchem_field_mapping: Path = Field(
        default=Path("extern/CATChem/drivers/nuopc/CATChem_field_mapping.yml"),
        description="The CATChem field mapping the generator extends with the emission import entries.",
    )
    catchem_species_file: Path = Field(
        default=Path("extern/CATChem/tests/Configs/Default/CATChem_species.yml"),
        description="The CATChem species file the emitted science config names; emission-map targets must be in it or be MET_* inputs.",
    )
    base_field_dictionary: Path = Field(
        default=Path("configs/prescribed/met_catchem/fd_protos.yaml"),
        description="The NUOPC field dictionary the generator extends with the emission standard names.",
    )
    cece_validator: Path = Field(
        default=Path("extern/CECE/scripts/validate_cece_config.py"),
        description="CECE's configuration validator; the generator runs it on the emitted CECE YAML when the file exists.",
    )
    n_tiles: int = Field(
        default=6,
        description="Tiles of the cubed-sphere grid; the PET count must be a multiple of it (one DE per PET).",
        gt=0,
    )
    standard_name_prefix: str = Field(
        default="extemis",
        description="Prefix of the emission standard names (<prefix>_<category>_<variable>) and of the CATChem mapping paths (<prefix>/<category>/<variable>).",
        min_length=1,
    )
    cece_config_filename: str = Field(
        default="cece.yaml",
        description="Name of the emitted CECE configuration under --out.",
    )
    field_dictionary_filename: str = Field(
        default="fd.yaml",
        description="Name of the emitted NUOPC field dictionary under --out.",
    )
    catchem_config_filename: str = Field(
        default="CATChem_new_config.yml",
        description="Name of the emitted CATChem science configuration under --out.",
    )
    catchem_emission_filename: str = Field(
        default="CATChem_emission.yml",
        description="Name of the emitted CATChem emission map under --out.",
    )
    catchem_mapping_filename: str = Field(
        default="CATChem_field_mapping.yml",
        description="Name of the emitted CATChem field mapping under --out.",
    )
    driver_config_filename: str = Field(
        default="esm.configure",
        description="Name of the emitted driver configuration under --out.",
    )
    gen_record_filename: str = Field(
        default="gen_configs_manifest.yaml",
        description="Name of the record the generator writes under --out (what was generated from which manifest).",
    )

    # tests
    fixture_dir: Path = Field(
        default=Path("data/fixtures/C48"),
        description="Where the test suite caches the real C48 mosaic and tile-1 files it downloads on demand (PROTOS_FIXTURE_DIR).",
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_case_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings, read once; `get_settings.cache_clear()` after changing the environment."""
    return Settings()
