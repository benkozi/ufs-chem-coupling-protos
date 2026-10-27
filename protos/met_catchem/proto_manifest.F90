!> The task-2 run manifest (data/<res>/manifest.yaml), read with ESMF_HConfig.
!!
!! Every key read here is a described field of the pydantic models in tools/manifest.py (Clock, FixFiles,
!! LandMask/Continent, MetParams/Tracer); nothing is read loosely, and a missing or mistyped key is an ESMF
!! error naming it. Relative paths in the manifest are relative to the directory make_mock_data was run
!! from; the caller joins them to its data root.
module proto_manifest
  use ESMF
  implicit none
  private

  public :: proto_tracer, proto_continent, proto_manifest_type, manifest_read, manifest_print, join_path

  integer, parameter, public :: PROTO_NAME_LEN = 32

  type proto_tracer
    character(len=PROTO_NAME_LEN) :: name = ''
    character(len=PROTO_NAME_LEN) :: units = ''
  end type proto_tracer

  type proto_continent
    real(ESMF_KIND_R8) :: lon = 0, lat = 0, semi_axis_lon = 1, semi_axis_lat = 1, rotation_deg = 0, exponent = 4
  end type proto_continent

  type proto_manifest_type
    character(len=8) :: resolution = ''
    type(ESMF_Time) :: start
    integer :: duration_hours = 0
    integer :: step_seconds = 0
    integer :: n_levels = 0
    character(len=ESMF_MAXPATHLEN) :: mosaic = ''
    character(len=ESMF_MAXPATHLEN) :: fix_directory = ''
    real(ESMF_KIND_R8) :: earth_radius_m = 0
    real(ESMF_KIND_R8) :: land_threshold = 0
    type(proto_continent), allocatable :: continents(:)
    ! met (MetParams, in the order of tools/manifest.py)
    real(ESMF_KIND_R8) :: surface_pressure_pa = 0
    real(ESMF_KIND_R8) :: temperature_equator_k = 0
    real(ESMF_KIND_R8) :: temperature_pole_k = 0
    real(ESMF_KIND_R8) :: lapse_rate_k_per_level = 0
    integer :: tropopause_level = 0
    real(ESMF_KIND_R8) :: zonal_wind_amplitude_m_s = 0
    real(ESMF_KIND_R8) :: pbl_height_m = 0
    real(ESMF_KIND_R8) :: soil_moisture = 0
    integer :: land_use_class = 0
    real(ESMF_KIND_R8) :: diurnal_amplitude_k = 0
    real(ESMF_KIND_R8) :: diurnal_peak_local_hour = 0
    integer :: n_soil_layers = 0
    real(ESMF_KIND_R8) :: pressure_top_pa = 0
    real(ESMF_KIND_R8) :: orography_max_m = 0
    real(ESMF_KIND_R8) :: specific_humidity_surface = 0
    real(ESMF_KIND_R8) :: cloud_fraction_max = 0
    real(ESMF_KIND_R8) :: friction_velocity_land_m_s = 0
    real(ESMF_KIND_R8) :: friction_velocity_ocean_m_s = 0
    real(ESMF_KIND_R8) :: roughness_length_land_cm = 0
    real(ESMF_KIND_R8) :: roughness_length_ocean_cm = 0
    real(ESMF_KIND_R8) :: shortwave_peak_w_m2 = 0
    real(ESMF_KIND_R8) :: sensible_heat_peak_w_m2 = 0
    real(ESMF_KIND_R8) :: latent_heat_peak_w_m2 = 0
    real(ESMF_KIND_R8) :: aerodynamic_conductance_s_m = 0
    real(ESMF_KIND_R8) :: canopy_resistance_s_m = 0
    real(ESMF_KIND_R8) :: leaf_area_index = 0
    real(ESMF_KIND_R8) :: vegetation_fraction = 0
    real(ESMF_KIND_R8) :: sea_ice_latitude_deg = 0
    real(ESMF_KIND_R8) :: snow_latitude_deg = 0
    integer :: soil_type_class = 0
    type(proto_tracer), allocatable :: tracers(:)
  end type proto_manifest_type

contains

  !> Join a manifest path to the data root unless it is absolute.
  function join_path(root, path) result(full)
    character(len=*), intent(in) :: root, path
    character(len=:), allocatable :: full
    if (len_trim(path) > 0 .and. path(1:1) == '/') then
      full = trim(path)
    else
      full = trim(root)//'/'//trim(path)
    end if
  end function join_path

  subroutine manifest_read(path, m, rc)
    character(len=*), intent(in) :: path
    type(proto_manifest_type), intent(out) :: m
    integer, intent(out) :: rc
    type(ESMF_HConfig) :: doc, node, seq, item
    character(len=:), allocatable :: text
    integer :: i, n

    rc = ESMF_SUCCESS
    doc = ESMF_HConfigCreate(filename=trim(path), rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="cannot read manifest "//trim(path), line=__LINE__, file=__FILE__)) return

    m%resolution = as_string(doc, "resolution", rc); if (rc /= ESMF_SUCCESS) return
    m%earth_radius_m = as_r8(doc, "earth_radius_m", rc); if (rc /= ESMF_SUCCESS) return

    node = at(doc, "clock", rc); if (rc /= ESMF_SUCCESS) return
    text = as_string(node, "start", rc); if (rc /= ESMF_SUCCESS) return
    if (len(text) >= 20) then
      if (text(20:20) == 'Z') text = text(1:19)   ! the manifest writes UTC stamps with a trailing Z
    end if
    call ESMF_TimeSet(m%start, timeString=text, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="manifest clock.start is not ISO 8601: "//text, line=__LINE__, file=__FILE__)) return
    m%duration_hours = as_i4(node, "duration_hours", rc); if (rc /= ESMF_SUCCESS) return
    m%step_seconds = as_i4(node, "step_seconds", rc); if (rc /= ESMF_SUCCESS) return
    m%n_levels = as_i4(node, "n_levels", rc); if (rc /= ESMF_SUCCESS) return
    call ESMF_HConfigDestroy(node, rc=rc); if (rc /= ESMF_SUCCESS) return

    node = at(doc, "fix", rc); if (rc /= ESMF_SUCCESS) return
    m%mosaic = as_string(node, "mosaic", rc); if (rc /= ESMF_SUCCESS) return
    m%fix_directory = as_string(node, "directory", rc); if (rc /= ESMF_SUCCESS) return
    call ESMF_HConfigDestroy(node, rc=rc); if (rc /= ESMF_SUCCESS) return

    node = at(doc, "land_mask", rc); if (rc /= ESMF_SUCCESS) return
    m%land_threshold = as_r8(node, "threshold", rc); if (rc /= ESMF_SUCCESS) return
    seq = at(node, "continents", rc); if (rc /= ESMF_SUCCESS) return
    n = ESMF_HConfigGetSize(seq, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="manifest land_mask.continents is not a sequence", line=__LINE__, file=__FILE__)) return
    allocate (m%continents(n))
    do i = 1, n
      item = ESMF_HConfigCreateAt(seq, index=i, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      m%continents(i)%lon = as_r8(item, "lon", rc); if (rc /= ESMF_SUCCESS) return
      m%continents(i)%lat = as_r8(item, "lat", rc); if (rc /= ESMF_SUCCESS) return
      m%continents(i)%semi_axis_lon = as_r8(item, "semi_axis_lon", rc); if (rc /= ESMF_SUCCESS) return
      m%continents(i)%semi_axis_lat = as_r8(item, "semi_axis_lat", rc); if (rc /= ESMF_SUCCESS) return
      m%continents(i)%rotation_deg = as_r8(item, "rotation_deg", rc); if (rc /= ESMF_SUCCESS) return
      m%continents(i)%exponent = as_r8(item, "exponent", rc); if (rc /= ESMF_SUCCESS) return
      call ESMF_HConfigDestroy(item, rc=rc); if (rc /= ESMF_SUCCESS) return
    end do
    call ESMF_HConfigDestroy(seq, rc=rc); if (rc /= ESMF_SUCCESS) return
    call ESMF_HConfigDestroy(node, rc=rc); if (rc /= ESMF_SUCCESS) return

    node = at(doc, "met", rc); if (rc /= ESMF_SUCCESS) return
    m%surface_pressure_pa = as_r8(node, "surface_pressure_pa", rc); if (rc /= ESMF_SUCCESS) return
    m%temperature_equator_k = as_r8(node, "temperature_equator_k", rc); if (rc /= ESMF_SUCCESS) return
    m%temperature_pole_k = as_r8(node, "temperature_pole_k", rc); if (rc /= ESMF_SUCCESS) return
    m%lapse_rate_k_per_level = as_r8(node, "lapse_rate_k_per_level", rc); if (rc /= ESMF_SUCCESS) return
    m%tropopause_level = as_i4(node, "tropopause_level", rc); if (rc /= ESMF_SUCCESS) return
    m%zonal_wind_amplitude_m_s = as_r8(node, "zonal_wind_amplitude_m_s", rc); if (rc /= ESMF_SUCCESS) return
    m%pbl_height_m = as_r8(node, "pbl_height_m", rc); if (rc /= ESMF_SUCCESS) return
    m%soil_moisture = as_r8(node, "soil_moisture", rc); if (rc /= ESMF_SUCCESS) return
    m%land_use_class = as_i4(node, "land_use_class", rc); if (rc /= ESMF_SUCCESS) return
    m%diurnal_amplitude_k = as_r8(node, "diurnal_amplitude_k", rc); if (rc /= ESMF_SUCCESS) return
    m%diurnal_peak_local_hour = as_r8(node, "diurnal_peak_local_hour", rc); if (rc /= ESMF_SUCCESS) return
    m%n_soil_layers = as_i4(node, "n_soil_layers", rc); if (rc /= ESMF_SUCCESS) return
    m%pressure_top_pa = as_r8(node, "pressure_top_pa", rc); if (rc /= ESMF_SUCCESS) return
    m%orography_max_m = as_r8(node, "orography_max_m", rc); if (rc /= ESMF_SUCCESS) return
    m%specific_humidity_surface = as_r8(node, "specific_humidity_surface", rc); if (rc /= ESMF_SUCCESS) return
    m%cloud_fraction_max = as_r8(node, "cloud_fraction_max", rc); if (rc /= ESMF_SUCCESS) return
    m%friction_velocity_land_m_s = as_r8(node, "friction_velocity_land_m_s", rc); if (rc /= ESMF_SUCCESS) return
    m%friction_velocity_ocean_m_s = as_r8(node, "friction_velocity_ocean_m_s", rc); if (rc /= ESMF_SUCCESS) return
    m%roughness_length_land_cm = as_r8(node, "roughness_length_land_cm", rc); if (rc /= ESMF_SUCCESS) return
    m%roughness_length_ocean_cm = as_r8(node, "roughness_length_ocean_cm", rc); if (rc /= ESMF_SUCCESS) return
    m%shortwave_peak_w_m2 = as_r8(node, "shortwave_peak_w_m2", rc); if (rc /= ESMF_SUCCESS) return
    m%sensible_heat_peak_w_m2 = as_r8(node, "sensible_heat_peak_w_m2", rc); if (rc /= ESMF_SUCCESS) return
    m%latent_heat_peak_w_m2 = as_r8(node, "latent_heat_peak_w_m2", rc); if (rc /= ESMF_SUCCESS) return
    m%aerodynamic_conductance_s_m = as_r8(node, "aerodynamic_conductance_s_m", rc); if (rc /= ESMF_SUCCESS) return
    m%canopy_resistance_s_m = as_r8(node, "canopy_resistance_s_m", rc); if (rc /= ESMF_SUCCESS) return
    m%leaf_area_index = as_r8(node, "leaf_area_index", rc); if (rc /= ESMF_SUCCESS) return
    m%vegetation_fraction = as_r8(node, "vegetation_fraction", rc); if (rc /= ESMF_SUCCESS) return
    m%sea_ice_latitude_deg = as_r8(node, "sea_ice_latitude_deg", rc); if (rc /= ESMF_SUCCESS) return
    m%snow_latitude_deg = as_r8(node, "snow_latitude_deg", rc); if (rc /= ESMF_SUCCESS) return
    m%soil_type_class = as_i4(node, "soil_type_class", rc); if (rc /= ESMF_SUCCESS) return
    seq = at(node, "tracers", rc); if (rc /= ESMF_SUCCESS) return
    n = ESMF_HConfigGetSize(seq, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="manifest met.tracers is not a sequence", line=__LINE__, file=__FILE__)) return
    if (n < 1) then
      call ESMF_LogSetError(ESMF_RC_ARG_BAD, msg="manifest met.tracers is empty", line=__LINE__, file=__FILE__, rcToReturn=rc)
      return
    end if
    allocate (m%tracers(n))
    do i = 1, n
      item = ESMF_HConfigCreateAt(seq, index=i, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      m%tracers(i)%name = as_string(item, "name", rc); if (rc /= ESMF_SUCCESS) return
      m%tracers(i)%units = as_string(item, "units", rc); if (rc /= ESMF_SUCCESS) return
      call ESMF_HConfigDestroy(item, rc=rc); if (rc /= ESMF_SUCCESS) return
    end do
    call ESMF_HConfigDestroy(seq, rc=rc); if (rc /= ESMF_SUCCESS) return
    call ESMF_HConfigDestroy(node, rc=rc); if (rc /= ESMF_SUCCESS) return
    call ESMF_HConfigDestroy(doc, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
  end subroutine manifest_read

  !> Print every value read, one per line, on the calling PET (the --dump-manifest mode of esmApp).
  subroutine manifest_print(m, unit)
    type(proto_manifest_type), intent(in) :: m
    integer, intent(in) :: unit
    character(len=32) :: stamp
    integer :: i, rc
    call ESMF_TimeGet(m%start, timeString=stamp, rc=rc)
    write (unit, '(a,a)') 'resolution=', trim(m%resolution)
    write (unit, '(a,a)') 'clock.start=', trim(stamp)
    write (unit, '(a,i0)') 'clock.duration_hours=', m%duration_hours
    write (unit, '(a,i0)') 'clock.step_seconds=', m%step_seconds
    write (unit, '(a,i0)') 'clock.n_levels=', m%n_levels
    write (unit, '(a,a)') 'fix.mosaic=', trim(m%mosaic)
    write (unit, '(a,a)') 'fix.directory=', trim(m%fix_directory)
    write (unit, '(a,es12.5)') 'earth_radius_m=', m%earth_radius_m
    write (unit, '(a,f6.3)') 'land_mask.threshold=', m%land_threshold
    do i = 1, size(m%continents)
      write (unit, '(a,i0,a,6(1x,f9.3))') 'land_mask.continents[', i, ']=', m%continents(i)%lon, m%continents(i)%lat, &
        m%continents(i)%semi_axis_lon, m%continents(i)%semi_axis_lat, m%continents(i)%rotation_deg, m%continents(i)%exponent
    end do
    write (unit, '(a,es12.5)') 'met.surface_pressure_pa=', m%surface_pressure_pa
    write (unit, '(a,f8.3)') 'met.temperature_equator_k=', m%temperature_equator_k
    write (unit, '(a,f8.3)') 'met.temperature_pole_k=', m%temperature_pole_k
    write (unit, '(a,f8.3)') 'met.lapse_rate_k_per_level=', m%lapse_rate_k_per_level
    write (unit, '(a,i0)') 'met.tropopause_level=', m%tropopause_level
    write (unit, '(a,f8.3)') 'met.zonal_wind_amplitude_m_s=', m%zonal_wind_amplitude_m_s
    write (unit, '(a,f8.1)') 'met.pbl_height_m=', m%pbl_height_m
    write (unit, '(a,f8.3)') 'met.soil_moisture=', m%soil_moisture
    write (unit, '(a,i0)') 'met.land_use_class=', m%land_use_class
    write (unit, '(a,f8.3)') 'met.diurnal_amplitude_k=', m%diurnal_amplitude_k
    write (unit, '(a,f8.3)') 'met.diurnal_peak_local_hour=', m%diurnal_peak_local_hour
    write (unit, '(a,i0)') 'met.n_soil_layers=', m%n_soil_layers
    write (unit, '(a,f10.3)') 'met.pressure_top_pa=', m%pressure_top_pa
    write (unit, '(a,f10.3)') 'met.orography_max_m=', m%orography_max_m
    write (unit, '(a,es12.5)') 'met.specific_humidity_surface=', m%specific_humidity_surface
    write (unit, '(a,f8.3)') 'met.cloud_fraction_max=', m%cloud_fraction_max
    write (unit, '(a,f8.3)') 'met.friction_velocity_land_m_s=', m%friction_velocity_land_m_s
    write (unit, '(a,f8.3)') 'met.friction_velocity_ocean_m_s=', m%friction_velocity_ocean_m_s
    write (unit, '(a,f8.3)') 'met.roughness_length_land_cm=', m%roughness_length_land_cm
    write (unit, '(a,f8.3)') 'met.roughness_length_ocean_cm=', m%roughness_length_ocean_cm
    write (unit, '(a,f8.1)') 'met.shortwave_peak_w_m2=', m%shortwave_peak_w_m2
    write (unit, '(a,f8.1)') 'met.sensible_heat_peak_w_m2=', m%sensible_heat_peak_w_m2
    write (unit, '(a,f8.1)') 'met.latent_heat_peak_w_m2=', m%latent_heat_peak_w_m2
    write (unit, '(a,f8.3)') 'met.aerodynamic_conductance_s_m=', m%aerodynamic_conductance_s_m
    write (unit, '(a,f8.1)') 'met.canopy_resistance_s_m=', m%canopy_resistance_s_m
    write (unit, '(a,f8.3)') 'met.leaf_area_index=', m%leaf_area_index
    write (unit, '(a,f8.3)') 'met.vegetation_fraction=', m%vegetation_fraction
    write (unit, '(a,f8.1)') 'met.sea_ice_latitude_deg=', m%sea_ice_latitude_deg
    write (unit, '(a,f8.1)') 'met.snow_latitude_deg=', m%snow_latitude_deg
    write (unit, '(a,i0)') 'met.soil_type_class=', m%soil_type_class
    do i = 1, size(m%tracers)
      write (unit, '(a,i0,a,a,1x,a)') 'met.tracers[', i, ']=', trim(m%tracers(i)%name), trim(m%tracers(i)%units)
    end do
  end subroutine manifest_print

  ! --- HConfig accessors: every miss is an error naming the key ---------------------------------------------

  function at(parent, key, rc) result(node)
    type(ESMF_HConfig), intent(in) :: parent
    character(len=*), intent(in) :: key
    integer, intent(out) :: rc
    type(ESMF_HConfig) :: node
    node = ESMF_HConfigCreateAt(parent, keyString=key, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="manifest key '"//key//"' is missing", line=__LINE__, file=__FILE__)) return
  end function at

  function as_r8(node, key, rc) result(value)
    type(ESMF_HConfig), intent(in) :: node
    character(len=*), intent(in) :: key
    integer, intent(out) :: rc
    real(ESMF_KIND_R8) :: value
    value = ESMF_HConfigAsR8(node, keyString=key, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="manifest key '"//key//"' is missing or not a number", line=__LINE__, file=__FILE__)) return
  end function as_r8

  function as_i4(node, key, rc) result(value)
    type(ESMF_HConfig), intent(in) :: node
    character(len=*), intent(in) :: key
    integer, intent(out) :: rc
    integer :: value
    value = ESMF_HConfigAsI4(node, keyString=key, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="manifest key '"//key//"' is missing or not an integer", line=__LINE__, file=__FILE__)) return
  end function as_i4

  function as_string(node, key, rc) result(value)
    type(ESMF_HConfig), intent(in) :: node
    character(len=*), intent(in) :: key
    integer, intent(out) :: rc
    character(len=:), allocatable :: value
    value = ESMF_HConfigAsString(node, keyString=key, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="manifest key '"//key//"' is missing or not a string", line=__LINE__, file=__FILE__)) return
  end function as_string

end module proto_manifest
