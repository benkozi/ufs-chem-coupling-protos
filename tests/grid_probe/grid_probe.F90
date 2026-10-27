!> grid_probe: what each PET can read from the task-2 mosaic grid (the task-4 corner-ring spike).
!!
!!   grid_probe <mosaic file> <tile directory/>
!!
!! Creates the grid exactly as proto_driver does (regDecompPTile 1 x petCount/6, CENTER and CORNER staggers)
!! and prints, per PET: the DE's tile (ESMF_GridGet localDE/tile), the exclusive and total bounds of the
!! centre and corner coordinate blocks (ESMF_GridGetCoord), the DE's tile-global index block from the
!! DistGrid (ESMF_DistGridGet minIndexPDe/maxIndexPDe/deToTileMap), a few coordinate values, and the
!! global cell-area check. The question it answers: does a DE own a closed (nx+1) x (ny+1) corner ring?
program grid_probe
  use ESMF
  implicit none

  type(ESMF_VM) :: vm
  type(ESMF_Grid) :: grid
  type(ESMF_DistGrid) :: distgrid
  type(ESMF_Field) :: area_field
  character(len=ESMF_MAXPATHLEN) :: mosaic, fix_dir
  integer :: rc, pet_count, local_pet, per_tile, t, tile_count, local_de_count, tile, de_count, dim_count, de
  integer :: reg_decomp(2, 6)
  integer, allocatable :: min_pde(:, :), max_pde(:, :), de_to_tile(:), local_de_to_de(:)
  real(ESMF_KIND_R8), pointer :: p2(:, :)
  real(ESMF_KIND_R8) :: area_sum(1), area_total
  character(len=512) :: line

  call ESMF_Initialize(defaultCalKind=ESMF_CALKIND_GREGORIAN, logkindflag=ESMF_LOGKIND_MULTI, vm=vm, rc=rc)
  if (rc /= ESMF_SUCCESS) error stop "grid_probe: ESMF_Initialize failed"
  call ESMF_VMGet(vm, petCount=pet_count, localPet=local_pet, rc=rc)
  call check(rc, "ESMF_VMGet")
  if (command_argument_count() < 2) call abort_with("usage: grid_probe <mosaic> <tile directory/>")
  call get_command_argument(1, mosaic)
  call get_command_argument(2, fix_dir)
  if (mod(pet_count, 6) /= 0) call abort_with("grid_probe: PET count must be a multiple of 6")
  per_tile = pet_count / 6
  do t = 1, 6
    reg_decomp(:, t) = (/1, per_tile/)
  end do

  grid = ESMF_GridCreateMosaic(filename=trim(mosaic), regDecompPTile=reg_decomp, tileFilePath=trim(fix_dir), &
                               staggerLocList=(/ESMF_STAGGERLOC_CENTER, ESMF_STAGGERLOC_CORNER/), name="probe", rc=rc)
  call check(rc, "ESMF_GridCreateMosaic")

  call ESMF_GridGet(grid, dimCount=dim_count, tileCount=tile_count, localDECount=local_de_count, distgrid=distgrid, rc=rc)
  call check(rc, "ESMF_GridGet (object-wide)")
  call ESMF_GridGet(grid, localDE=0, tile=tile, rc=rc)
  call check(rc, "ESMF_GridGet (localDE tile)")
  write (line, '(a,i0,a,i0,a,i0,a,i0,a,i0)') "probe PET ", local_pet, ": tileCount=", tile_count, " localDECount=", local_de_count, &
    " tile=", tile, " dimCount=", dim_count
  call say(line)

  call describe_stagger(grid, ESMF_STAGGERLOC_CENTER, "CENTER", local_pet)
  call describe_stagger(grid, ESMF_STAGGERLOC_CORNER, "CORNER", local_pet)

  ! DistGrid view: every DE's tile-global index block and tile
  call ESMF_DistGridGet(distgrid, deCount=de_count, rc=rc)
  call check(rc, "ESMF_DistGridGet deCount")
  allocate (min_pde(dim_count, de_count), max_pde(dim_count, de_count), de_to_tile(de_count), local_de_to_de(local_de_count))
  call ESMF_DistGridGet(distgrid, minIndexPDe=min_pde, maxIndexPDe=max_pde, deToTileMap=de_to_tile, &
                        localDeToDeMap=local_de_to_de, rc=rc)
  call check(rc, "ESMF_DistGridGet per DE")
  de = local_de_to_de(1)
  write (line, '(a,i0,a,i0,a,i0,a,i0,a,i0,a,i0,a,i0)') "probe PET ", local_pet, ": DistGrid de=", de, " tile=", de_to_tile(de + 1), &
    " minIndexPDe=", min_pde(1, de + 1), ",", min_pde(2, de + 1), " maxIndexPDe=", max_pde(1, de + 1), ",", max_pde(2, de + 1)
  call say(line)
  if (local_pet == 0) then
    do de = 0, de_count - 1
      write (line, '(a,i0,a,i0,a,i0,a,i0,a,i0,a,i0)') "probe all DEs: de=", de, " tile=", de_to_tile(de + 1), " block ", &
        min_pde(1, de + 1), "..", max_pde(1, de + 1), " x ", min_pde(2, de + 1), "..", max_pde(2, de + 1)
      call say(line)
    end do
  end if

  ! The area check of task 3 (square radians on the unit sphere)
  area_field = ESMF_FieldCreate(grid, typekind=ESMF_TYPEKIND_R8, staggerloc=ESMF_STAGGERLOC_CENTER, rc=rc)
  call check(rc, "ESMF_FieldCreate area")
  call ESMF_FieldRegridGetArea(area_field, rc=rc)
  call check(rc, "ESMF_FieldRegridGetArea")
  nullify (p2)
  call ESMF_FieldGet(area_field, farrayPtr=p2, rc=rc)
  call check(rc, "ESMF_FieldGet area")
  area_sum(1) = sum(p2)
  call ESMF_VMAllFullReduce(vm, area_sum, area_total, 1, ESMF_REDUCE_SUM, rc=rc)
  call check(rc, "ESMF_VMAllFullReduce")
  write (line, '(a,i0,a,f12.8)') "probe PET ", local_pet, ": global area / 4pi = ", area_total / (4.0d0 * acos(-1.0d0))
  call say(line)
  if (local_pet == 0) print '(a)', "grid_probe: done"
  call ESMF_Finalize(rc=rc)

contains

  subroutine describe_stagger(grid, stagger, label, local_pet)
    type(ESMF_Grid), intent(in) :: grid
    type(ESMF_StaggerLoc), intent(in) :: stagger
    character(len=*), intent(in) :: label
    integer, intent(in) :: local_pet
    real(ESMF_KIND_R8), pointer :: lon(:, :), lat(:, :)
    integer :: elb(2), eub(2), tlb(2), tub(2), clb(2), cub(2), nx, ny, rc
    character(len=512) :: line
    nullify (lon, lat)
    call ESMF_GridGetCoord(grid, coordDim=1, staggerloc=stagger, localDE=0, farrayPtr=lon, exclusiveLBound=elb, &
                           exclusiveUBound=eub, computationalLBound=clb, computationalUBound=cub, totalLBound=tlb, totalUBound=tub, rc=rc)
    call check(rc, "ESMF_GridGetCoord dim 1 "//label)
    call ESMF_GridGetCoord(grid, coordDim=2, staggerloc=stagger, localDE=0, farrayPtr=lat, rc=rc)
    call check(rc, "ESMF_GridGetCoord dim 2 "//label)
    nx = size(lon, 1); ny = size(lon, 2)
    write (line, '(a,i0,a,a,a,i0,a,i0,a,i0,a,i0,a,i0,a,i0,a,i0,a,i0,a,i0,a,i0,a,i0,a,i0,a,i0,a,i0)') &
      "probe PET ", local_pet, ": ", label, " pointer ", nx, "x", ny, " exclusive ", elb(1), "..", eub(1), " x ", elb(2), "..", eub(2), &
      " computational ", clb(1), "..", cub(1), " x ", clb(2), "..", cub(2), " total ", tlb(1), "..", tub(1), " x ", tlb(2), "..", tub(2)
    call say(line)
    write (line, '(a,i0,a,a,a,f10.4,a,f9.4,a,f10.4,a,f9.4,a,f10.4,a,f10.4)') "probe PET ", local_pet, ": ", label, &
      " first (", lon(lbound(lon, 1), lbound(lon, 2)), ",", lat(lbound(lat, 1), lbound(lat, 2)), ") last (", &
      lon(ubound(lon, 1), ubound(lon, 2)), ",", lat(ubound(lat, 1), ubound(lat, 2)), ") lon range ", minval(lon), "..", maxval(lon)
    call say(line)
  end subroutine describe_stagger

  subroutine say(text)
    character(len=*), intent(in) :: text
    print '(a)', trim(text)
    flush (6)
    call ESMF_LogWrite(trim(text), ESMF_LOGMSG_INFO)
  end subroutine say

  subroutine check(code, what)
    integer, intent(in) :: code
    character(len=*), intent(in) :: what
    if (code == ESMF_SUCCESS) return
    if (ESMF_LogFoundError(rcToCheck=code, msg="grid_probe: "//what//" failed", line=__LINE__, file=__FILE__)) &
      call abort_with("grid_probe: "//what//" failed (see PET*.ESMF_LogFile)")
  end subroutine check

  subroutine abort_with(msg)
    character(len=*), intent(in) :: msg
    integer :: ignore
    print '(a)', msg
    flush (6)
    call ESMF_LogWrite(msg, ESMF_LOGMSG_ERROR, rc=ignore)
    call ESMF_Finalize(endflag=ESMF_END_ABORT, rc=ignore)
    error stop 1
  end subroutine abort_with

end program grid_probe
