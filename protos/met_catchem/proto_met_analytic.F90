!> Closed-form meteorology for the MET provider: pure functions of longitude, latitude, level index and time,
!! with every constant taken from the manifest's `met` and `land_mask` sections. Levels are counted from the
!! surface (k = 1 at the surface), as the FV3 cap exports them and the CATChem cap consumes them.
module proto_met_analytic
  use ESMF, only: ESMF_KIND_R8
  use proto_manifest, only: proto_manifest_type
  implicit none
  private

  integer, parameter, public :: rk = ESMF_KIND_R8
  real(rk), parameter, public :: PI = 3.14159265358979323846_rk
  real(rk), parameter, public :: DEG2RAD = PI / 180.0_rk
  real(rk), parameter, public :: G0 = 9.80665_rk          !< m s-2
  real(rk), parameter, public :: R_DRY = 287.05_rk        !< J kg-1 K-1
  real(rk), parameter, public :: SCALE_HEIGHT_M = 8000.0_rk
  real(rk), parameter, public :: DECLINATION_MAX_DEG = 23.44_rk

  public :: wrap_degrees, land_fraction, local_solar_hour, cos_zenith, surface_temperature, orography, &
    surface_pressure, column

contains

  !> Longitude difference wrapped into [-180, 180).
  elemental function wrap_degrees(x) result(w)
    real(rk), intent(in) :: x
    real(rk) :: w
    w = modulo(x + 180.0_rk, 360.0_rk) - 180.0_rk
  end function wrap_degrees

  !> Clipped sum of the manifest's super-Gaussian continents, in [0, 1]: the formula of tools/inventories.py.
  function land_fraction(m, lon, lat) result(f)
    type(proto_manifest_type), intent(in) :: m
    real(rk), intent(in) :: lon, lat
    real(rk) :: f
    real(rk) :: dx, dy, theta, u, v, r2
    integer :: c
    f = 0.0_rk
    do c = 1, size(m%continents)
      dx = wrap_degrees(lon - m%continents(c)%lon)
      dy = lat - m%continents(c)%lat
      theta = m%continents(c)%rotation_deg * DEG2RAD
      u = dx * cos(theta) + dy * sin(theta)
      v = -dx * sin(theta) + dy * cos(theta)
      r2 = (u / m%continents(c)%semi_axis_lon)**2 + (v / m%continents(c)%semi_axis_lat)**2
      f = f + exp(-(r2**(m%continents(c)%exponent / 2.0_rk)))
    end do
    f = min(max(f, 0.0_rk), 1.0_rk)
  end function land_fraction

  !> Local solar hour in [0, 24) from the UTC hour and the longitude.
  elemental function local_solar_hour(hour_utc, lon) result(h)
    real(rk), intent(in) :: hour_utc, lon
    real(rk) :: h
    h = modulo(hour_utc + lon / 15.0_rk, 24.0_rk)
  end function local_solar_hour

  !> Cosine of the solar zenith angle (declination from the day of year, hour angle from the local solar hour).
  elemental function cos_zenith(lat, hour_local, day_of_year) result(c)
    real(rk), intent(in) :: lat, hour_local
    integer, intent(in) :: day_of_year
    real(rk) :: c
    real(rk) :: decl, hour_angle
    decl = DECLINATION_MAX_DEG * DEG2RAD * sin(2.0_rk * PI * real(day_of_year - 81, rk) / 365.0_rk)
    hour_angle = 15.0_rk * (hour_local - 12.0_rk) * DEG2RAD
    c = sin(lat * DEG2RAD) * sin(decl) + cos(lat * DEG2RAD) * cos(decl) * cos(hour_angle)
  end function cos_zenith

  !> Surface air temperature: pole-to-equator cos^2 profile plus the diurnal cycle in local solar time.
  pure function surface_temperature(m, lat, hour_local) result(t)
    type(proto_manifest_type), intent(in) :: m
    real(rk), intent(in) :: lat, hour_local
    real(rk) :: t
    t = m%temperature_pole_k + (m%temperature_equator_k - m%temperature_pole_k) * cos(lat * DEG2RAD)**2 &
      + m%diurnal_amplitude_k * cos(2.0_rk * PI * (hour_local - m%diurnal_peak_local_hour) / 24.0_rk)
  end function surface_temperature

  !> Surface height, m: the land fraction times cos^2 latitude, scaled to the manifest's peak.
  pure function orography(m, land, lat) result(z)
    type(proto_manifest_type), intent(in) :: m
    real(rk), intent(in) :: land, lat
    real(rk) :: z
    z = m%orography_max_m * land * cos(lat * DEG2RAD)**2
  end function orography

  !> Surface pressure, Pa: the manifest's sea-level pressure reduced with a fixed scale height.
  pure function surface_pressure(m, oro) result(p)
    type(proto_manifest_type), intent(in) :: m
    real(rk), intent(in) :: oro
    real(rk) :: p
    p = m%surface_pressure_pa * exp(-oro / SCALE_HEIGHT_M)
  end function surface_pressure

  !> One column: interface pressures (nz+1, k = 1 surface), mid-level pressures, temperatures, geopotential at
  !! interfaces and mid levels (m2 s-2, hypsometric), zonal and meridional wind, cloud fraction and specific
  !! humidity at mid levels.
  pure subroutine column(m, nz, ps, ts, oro, lon, lat, pedge, pmid, temp, phi_edge, phi_mid, u, v, cldf, q)
    type(proto_manifest_type), intent(in) :: m
    integer, intent(in) :: nz
    real(rk), intent(in) :: ps, ts, oro, lon, lat
    real(rk), intent(out) :: pedge(nz + 1), pmid(nz), temp(nz), phi_edge(nz + 1), phi_mid(nz), u(nz), v(nz), cldf(nz), q(nz)
    integer :: k
    real(rk) :: sigma, sigma_mid
    do k = 1, nz + 1
      sigma = real(nz + 1 - k, rk) / real(nz, rk)
      pedge(k) = m%pressure_top_pa + (ps - m%pressure_top_pa) * sigma**2
    end do
    do k = 1, nz
      pmid(k) = 0.5_rk * (pedge(k) + pedge(k + 1))
      temp(k) = ts - m%lapse_rate_k_per_level * real(min(k - 1, m%tropopause_level), rk)
    end do
    phi_edge(1) = G0 * oro
    do k = 1, nz
      phi_edge(k + 1) = phi_edge(k) + R_DRY * temp(k) * log(pedge(k) / pedge(k + 1))
      phi_mid(k) = 0.5_rk * (phi_edge(k) + phi_edge(k + 1))
    end do
    do k = 1, nz
      sigma_mid = pmid(k) / ps
      u(k) = m%zonal_wind_amplitude_m_s * cos(lat * DEG2RAD)**2 * (1.0_rk - sigma_mid)
      v(k) = 0.1_rk * m%zonal_wind_amplitude_m_s * sin(lon * DEG2RAD) * cos(lat * DEG2RAD) * (1.0_rk - sigma_mid)
      cldf(k) = m%cloud_fraction_max * 4.0_rk * sigma_mid * (1.0_rk - sigma_mid) * 0.5_rk * (1.0_rk + cos(2.0_rk * lat * DEG2RAD))
      q(k) = m%specific_humidity_surface * sigma_mid**2
    end do
  end subroutine column

end module proto_met_analytic
