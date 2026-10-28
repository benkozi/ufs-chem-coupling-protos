"""The config generator's parameter set: what the emitted files need that the manifest does not carry.

The checked-in file is `tools/defaults/gen_configs.yaml` (`--defaults` / `PROTOS_GEN_DEFAULTS` select another):
per category the CATChem settings and the emission map (file variable -> species, scale), the processes and
diagnostics of the science config, the run sequence and the prototype name. Every model forbids unknown keys
and describes every field; a test pins the file's digest, so a change is a change to the code.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from tools.manifest import Strict

ApplyMethod = Literal["add", "replace"]


class EmissionMapVariable(Strict):
    long_name: str = Field(
        description="Descriptive name written to the CATChem emission map."
    )
    scale: list[float] = Field(
        description="Scale factor per mapped species (the emission map's `scale`), same length as `map`."
    )
    map: list[str] = Field(
        description="CATChem species, or MET_* met-state targets, the file variable feeds; empty when it is not applied."
    )

    @model_validator(mode="after")
    def _lengths_agree(self) -> EmissionMapVariable:
        if len(self.scale) != len(self.map):
            raise ValueError("scale and map must have the same length")
        return self


class CategoryDefaults(Strict):
    apply_method: ApplyMethod = Field(
        description="CATChem apply_method: add accumulates an emission flux, replace overwrites a concentration or met input."
    )
    vertical_dist: str = Field(
        default="none",
        description="CATChem vertical_dist of the category (none, P100, P500, Ppbl, aviation, ...).",
    )
    plume_rise: str | None = Field(
        default=None,
        description="CATChem plume_rise scheme of the category, or null for none.",
    )
    diagnostics: bool = Field(
        default=False, description="CATChem per-category diagnostics switch."
    )
    variables: dict[str, EmissionMapVariable] = Field(
        description="Emission map entries by file variable name (the manifest's variable names)."
    )


class DiagnosticsDefaults(Strict):
    enabled: bool = Field(
        description="Whether the cap writes its NetCDF diagnostics (aqmio)."
    )
    directory: str = Field(
        description="Output directory of the diagnostics, relative to the run directory."
    )
    prefix: str = Field(description="File name prefix of the diagnostics.")
    frequency: int = Field(description="Diagnostics write interval in seconds.", gt=0)
    format: str = Field(description="Diagnostics format: netcdf or latlon.")
    diag_list: list[str] = Field(description="Species written to the diagnostics.")


class GenDefaults(Strict):
    prototype: str = Field(
        description="The run's name: `prototype:` of the driver config and the science config's simulation name."
    )
    verbosity: str = Field(
        description="NUOPC Verbosity attribute given to every component in the driver config."
    )
    processes: list[str] = Field(
        description="Processes of the science config's run phase (extemis is switched on separately)."
    )
    seasalt_scheme: str = Field(description="Sea-salt scheme of the science config.")
    diagnostics: DiagnosticsDefaults = Field(
        description="The science config's diagnostics.output block."
    )
    run_sequence: list[str] = Field(
        description="Lines of the driver's runSeq:: block inside the coupling-step loop, in order.",
        min_length=1,
    )
    categories: dict[str, CategoryDefaults] = Field(
        description="Per-category CATChem settings and emission map, by manifest category name."
    )
