!> The prototype driver (NUOPC_Driver), mirroring UFSDriver.F90's mechanics on an ESMF_Config of the
!! ufs.configure shape: a component list (EARTH_component_list: any of CATCHEM, MET, CECE, in the order they
!! are added), per-component PET lists and attribute blocks, and a runSeq:: block ingested with
!! NUOPC_DriverIngestRunSequence. It reads the task-2 manifest for the clock and the fix-file paths, creates
!! the cubed-sphere grid once with ESMF_GridCreateMosaic and attaches it to MET and CECE (CATChem takes its
!! grid from the shared rank-4 tracer import). One DE per PET is enforced here, because the CATChem cap's own
!! localDeCount check only logs and CECE's host-block path needs it too.
module proto_driver
  use ESMF
  use NUOPC
  use NUOPC_Driver, only: driverSS => SetServices, label_SetModelServices, label_SetRunSequence, &
                          NUOPC_DriverAddComp, NUOPC_DriverIngestRunSequence, NUOPC_DriverPrint
  use cc_nuopc, only: catchem_SS => SetServices, catchem_SV => SetVM, cc_nuopc_set_config_file, cc_nuopc_set_field_mapping_file
  use cece_cap_mod, only: cece_SS => CECE_SetServices, CECE_SetConfigPath
  use proto_met, only: met_SS => SetServices, proto_met_configure
  use proto_totals, only: hosted_spec
  use proto_manifest
  implicit none
  private

  public :: SetServices

  integer, parameter :: N_TILES = 6

contains

  subroutine SetServices(driver, rc)
    type(ESMF_GridComp) :: driver
    integer, intent(out) :: rc
    rc = ESMF_SUCCESS
    call NUOPC_CompDerive(driver, driverSS, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call NUOPC_CompSpecialize(driver, specLabel=label_SetModelServices, specRoutine=SetModelServices, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call NUOPC_CompSpecialize(driver, specLabel=label_SetRunSequence, specRoutine=SetRunSequence, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
  end subroutine SetServices

  subroutine SetModelServices(driver, rc)
    type(ESMF_GridComp) :: driver
    integer, intent(out) :: rc
    type(ESMF_Config) :: config
    type(ESMF_VM) :: vm
    type(ESMF_Clock) :: clock
    type(ESMF_Time) :: stop_time
    type(ESMF_TimeInterval) :: step, duration
    type(ESMF_Grid) :: grid
    type(ESMF_GridComp) :: child
    type(proto_manifest_type) :: m
    type(hosted_spec), allocatable :: hosted(:)
    character(len=ESMF_MAXPATHLEN) :: manifest, data_root, catchem_config, cece_config, mapping
    character(len=ESMF_MAXSTR), allocatable :: comp_labels(:)
    character(len=32) :: grid_source
    character(len=:), allocatable :: mosaic, fix_dir
    character(len=ESMF_MAXSTR) :: msg
    integer :: pet_count, local_pet, per_tile, local_de_count, tile_count, tile_size, t, n_comps, i
    integer :: reg_decomp(2, N_TILES), decomp(2)
    logical :: have_decomp

    rc = ESMF_SUCCESS
    call ESMF_GridCompGet(driver, config=config, vm=vm, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call ESMF_VMGet(vm, petCount=pet_count, localPet=local_pet, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return

    ! --- config keys ---
    call ESMF_ConfigGetAttribute(config, manifest, label="manifest:", rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="config key 'manifest:' missing", line=__LINE__, file=__FILE__)) return
    call ESMF_ConfigGetAttribute(config, data_root, label="data_root:", default=".", rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call ESMF_ConfigGetAttribute(config, mapping, label="catchem_field_mapping_file:", rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="config key 'catchem_field_mapping_file:' missing", line=__LINE__, file=__FILE__)) return
    call ESMF_ConfigFindLabel(config, "regdecomp_per_tile:", isPresent=have_decomp, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    ! the component list, the UFSDriver way (ESMF_ConfigGetLen, then the value list)
    n_comps = ESMF_ConfigGetLen(config, label="EARTH_component_list:", rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="config key 'EARTH_component_list:' missing", line=__LINE__, file=__FILE__)) return
    if (n_comps < 1) then
      call fail(local_pet, "driver: EARTH_component_list: names no component", rc)
      return
    end if
    allocate (comp_labels(n_comps))
    call ESMF_ConfigGetAttribute(config, valueList=comp_labels, label="EARTH_component_list:", count=n_comps, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="config key 'EARTH_component_list:' unreadable", line=__LINE__, file=__FILE__)) return
    call read_hosted_imports(config, hosted, rc); if (rc /= ESMF_SUCCESS) return

    ! --- the manifest: clock and fix files ---
    call manifest_read(trim(manifest), m, rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="driver: manifest "//trim(manifest), line=__LINE__, file=__FILE__)) return
    call ESMF_TimeIntervalSet(step, s=m%step_seconds, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call ESMF_TimeIntervalSet(duration, h=m%duration_hours, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    stop_time = m%start + duration
    clock = ESMF_ClockCreate(name="met_catchem clock", timeStep=step, startTime=m%start, stopTime=stop_time, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call ESMF_GridCompSet(driver, clock=clock, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return

    ! --- the PET rule: six tiles, one DE per PET ---
    if (mod(pet_count, N_TILES) /= 0) then
      write (msg, '(a,i0,a)') "met_catchem: PET count ", pet_count, " must be a multiple of 6 (one DE per PET on six tiles)"
      call fail(local_pet, trim(msg), rc)
      return
    end if
    per_tile = pet_count / N_TILES
    if (have_decomp) then
      call ESMF_ConfigGetAttribute(config, decomp, count=2, label="regdecomp_per_tile:", rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg="config key 'regdecomp_per_tile:' needs two integers", line=__LINE__, file=__FILE__)) return
      if (decomp(1) * decomp(2) /= per_tile) then
        write (msg, '(a,i0,a,i0,a,i0,a)') "met_catchem: regdecomp_per_tile ", decomp(1), " x ", decomp(2), &
          " must equal the PET count per tile ", per_tile, " (one DE per PET)"
        call fail(local_pet, trim(msg), rc)
        return
      end if
    else
      decomp = (/1, per_tile/)
    end if
    do t = 1, N_TILES
      reg_decomp(:, t) = decomp
    end do

    ! --- the grid, once: from the task-2 mosaic (tileFilePath: the mosaic's gridlocation is a stale HPC path),
    !     or analytic for the file-free debug mode ---
    call ESMF_ConfigGetAttribute(config, grid_source, label="grid_source:", default="mosaic", rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    select case (trim(grid_source))
    case ("mosaic")
      mosaic = join_path(trim(data_root), trim(m%mosaic))
      fix_dir = join_path(trim(data_root), trim(m%fix_directory))//'/'
      grid = ESMF_GridCreateMosaic(filename=mosaic, regDecompPTile=reg_decomp, tileFilePath=fix_dir, &
                                   staggerLocList=(/ESMF_STAGGERLOC_CENTER, ESMF_STAGGERLOC_CORNER/), name=trim(m%resolution), rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg="driver: ESMF_GridCreateMosaic "//mosaic, line=__LINE__, file=__FILE__)) return
    case ("analytic")
      read (m%resolution(2:), *, iostat=t) tile_size
      if (t /= 0 .or. tile_size <= 0) then
        call fail(local_pet, "met_catchem: cannot read the tile size from resolution '"//trim(m%resolution)//"'", rc)
        return
      end if
      grid = ESMF_GridCreateCubedSphere(tileSize=tile_size, regDecompPTile=reg_decomp, coordSys=ESMF_COORDSYS_SPH_DEG, &
                                        staggerLocList=(/ESMF_STAGGERLOC_CENTER, ESMF_STAGGERLOC_CORNER/), name=trim(m%resolution)//"_analytic", rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg="driver: ESMF_GridCreateCubedSphere", line=__LINE__, file=__FILE__)) return
      call ESMF_LogWrite("driver: grid_source analytic (ESMF_GridCreateCubedSphere, debug mode; the prototype's grid is the mosaic)", &
                         ESMF_LOGMSG_WARNING)
    case default
      call fail(local_pet, "met_catchem: grid_source must be mosaic or analytic, got '"//trim(grid_source)//"'", rc)
      return
    end select
    call ESMF_GridGet(grid, localDECount=local_de_count, tileCount=tile_count, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    if (local_de_count /= 1) then
      write (msg, '(a,i0,a,i0,a)') "met_catchem: PET ", local_pet, " holds ", local_de_count, " DEs; exactly one is required"
      call fail(local_pet, trim(msg), rc)
      return
    end if
    write (msg, '(a,a,a,i0,a,i0,a,i0,a,i0,a)') "driver: grid ", trim(m%resolution), " tileCount=", tile_count, &
      " regDecompPTile=", decomp(1), "x", decomp(2), " per tile, petCount=", pet_count, ", one DE per PET"
    call ESMF_LogWrite(trim(msg), ESMF_LOGMSG_INFO)
    if (local_pet == 0) print '(a)', trim(msg)

    ! --- components, in the order of EARTH_component_list (Advertise runs in that order); the grid goes
    !     to MET and CECE, never to CATChem ---
    call cc_nuopc_set_field_mapping_file(trim(mapping))
    call proto_met_configure(trim(manifest), trim(data_root), trim(mapping), hosted)
    do i = 1, n_comps
      select case (trim(comp_labels(i)))
      case ("CATCHEM")
        call ESMF_ConfigGetAttribute(config, catchem_config, label="catchem_config_file:", rc=rc)
        if (ESMF_LogFoundError(rcToCheck=rc, msg="config key 'catchem_config_file:' missing (CATCHEM is listed)", line=__LINE__, file=__FILE__)) return
        call cc_nuopc_set_config_file(trim(catchem_config))
        call add_component(driver, config, "CATCHEM", pet_count, child, rc, catchem_SS, catchem_SV); if (rc /= ESMF_SUCCESS) return
      case ("MET")
        call add_component(driver, config, "MET", pet_count, child, rc, met_SS); if (rc /= ESMF_SUCCESS) return
        call ESMF_GridCompSet(child, grid=grid, rc=rc)
        if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      case ("CECE")
        call ESMF_ConfigGetAttribute(config, cece_config, label="cece_config_file:", rc=rc)
        if (ESMF_LogFoundError(rcToCheck=rc, msg="config key 'cece_config_file:' missing (CECE is listed)", line=__LINE__, file=__FILE__)) return
        call CECE_SetConfigPath(trim(cece_config), rc)
        if (ESMF_LogFoundError(rcToCheck=rc, msg="CECE_SetConfigPath "//trim(cece_config), line=__LINE__, file=__FILE__)) return
        call add_component(driver, config, "CECE", pet_count, child, rc, cece_SS); if (rc /= ESMF_SUCCESS) return
        call ESMF_GridCompSet(child, grid=grid, rc=rc)
        if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      case default
        call fail(local_pet, "driver: unknown component '"//trim(comp_labels(i))//"' in EARTH_component_list (CATCHEM, MET, CECE)", rc)
        return
      end select
    end do
  end subroutine SetModelServices

  !> The optional `met_hosted_imports::` table: one row per field MET hosts as a reference-shared import,
  !! `<standard name> <category> <variable> <source file>` (the last three name the totals record).
  subroutine read_hosted_imports(config, hosted, rc)
    type(ESMF_Config), intent(inout) :: config
    type(hosted_spec), allocatable, intent(out) :: hosted(:)
    integer, intent(out) :: rc
    type(NUOPC_FreeFormat) :: table
    character(len=NUOPC_FreeFormatLen), allocatable :: tokens(:)
    integer :: n, i, n_tokens
    character(len=ESMF_MAXSTR) :: msg
    rc = ESMF_SUCCESS
    table = NUOPC_FreeFormatCreate(config, label="met_hosted_imports::", relaxedflag=.true., rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call NUOPC_FreeFormatGet(table, lineCount=n, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    allocate (hosted(n))
    do i = 1, n
      call NUOPC_FreeFormatGetLine(table, line=i, tokenCount=n_tokens, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      if (n_tokens /= 4) then
        write (msg, '(a,i0,a,i0,a)') "driver: met_hosted_imports:: row ", i, " has ", n_tokens, &
          " tokens; expected <standard name> <category> <variable> <source file>"
        call ESMF_LogSetError(ESMF_RC_ARG_BAD, msg=trim(msg), line=__LINE__, file=__FILE__, rcToReturn=rc)
        return
      end if
      allocate (tokens(n_tokens))
      call NUOPC_FreeFormatGetLine(table, line=i, tokenList=tokens, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      hosted(i)%standard_name = trim(tokens(1))
      hosted(i)%category = trim(tokens(2))
      hosted(i)%variable = trim(tokens(3))
      hosted(i)%file = trim(tokens(4))
      deallocate (tokens)
    end do
    call NUOPC_FreeFormatDestroy(table, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    if (n > 0) then
      write (msg, '(a,i0,a)') "driver: MET hosts ", n, " import(s) from met_hosted_imports::"
      call ESMF_LogWrite(trim(msg), ESMF_LOGMSG_INFO)
    end if
  end subroutine read_hosted_imports

  !> Add a child on its <PREFIX>_petlist_bounds: (default all PETs) and ingest <PREFIX>_attributes::.
  subroutine add_component(driver, config, prefix, pet_count, comp, rc, set_services, set_vm)
    type(ESMF_GridComp) :: driver
    type(ESMF_Config), intent(inout) :: config
    character(len=*), intent(in) :: prefix
    integer, intent(in) :: pet_count
    type(ESMF_GridComp), intent(out) :: comp
    integer, intent(out) :: rc
    interface
      recursive subroutine set_services(gridcomp, rc)
        use ESMF
        implicit none
        type(ESMF_GridComp) :: gridcomp
        integer, intent(out) :: rc
      end subroutine set_services
      recursive subroutine set_vm(gridcomp, rc)
        use ESMF
        implicit none
        type(ESMF_GridComp) :: gridcomp
        integer, intent(out) :: rc
      end subroutine set_vm
    end interface
    optional :: set_vm
    integer :: bounds(2), i
    integer, allocatable :: pet_list(:)
    type(NUOPC_FreeFormat) :: attributes
    character(len=ESMF_MAXSTR) :: msg

    rc = ESMF_SUCCESS
    call ESMF_ConfigGetAttribute(config, bounds, count=2, label=prefix//"_petlist_bounds:", default=-1, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="config key '"//prefix//"_petlist_bounds:'", line=__LINE__, file=__FILE__)) return
    if (bounds(1) < 0) bounds = (/0, pet_count - 1/)
    if (bounds(2) >= pet_count .or. bounds(1) > bounds(2)) then
      write (msg, '(a,a,a,i0,1x,i0,a,i0,a)') "met_catchem: ", prefix, "_petlist_bounds ", bounds(1), bounds(2), &
        " outside 0..", pet_count - 1, ""
      call ESMF_LogSetError(ESMF_RC_ARG_BAD, msg=trim(msg), line=__LINE__, file=__FILE__, rcToReturn=rc)
      return
    end if
    allocate (pet_list(bounds(2) - bounds(1) + 1))
    pet_list = (/(i, i=bounds(1), bounds(2))/)
    if (present(set_vm)) then
      call NUOPC_DriverAddComp(driver, prefix, compSetServicesRoutine=set_services, compSetVMRoutine=set_vm, &
                               petList=pet_list, comp=comp, rc=rc)
    else
      call NUOPC_DriverAddComp(driver, prefix, compSetServicesRoutine=set_services, petList=pet_list, comp=comp, rc=rc)
    end if
    if (ESMF_LogFoundError(rcToCheck=rc, msg="driver: add "//prefix, line=__LINE__, file=__FILE__)) return
    attributes = NUOPC_FreeFormatCreate(config, label=prefix//"_attributes::", relaxedflag=.true., rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call NUOPC_CompAttributeIngest(comp, attributes, addFlag=.true., rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call NUOPC_FreeFormatDestroy(attributes, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    write (msg, '(a,a,a,i0,a,i0)') "driver: added ", prefix, " on PETs ", bounds(1), "..", bounds(2)
    call ESMF_LogWrite(trim(msg), ESMF_LOGMSG_INFO)
  end subroutine add_component

  subroutine SetRunSequence(driver, rc)
    type(ESMF_GridComp) :: driver
    integer, intent(out) :: rc
    type(ESMF_Config) :: config
    type(NUOPC_FreeFormat) :: run_seq
    rc = ESMF_SUCCESS
    call ESMF_GridCompGet(driver, config=config, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    run_seq = NUOPC_FreeFormatCreate(config, label="runSeq::", rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="config block 'runSeq::' missing", line=__LINE__, file=__FILE__)) return
    call NUOPC_DriverIngestRunSequence(driver, run_seq, autoAddConnectors=.true., rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call NUOPC_FreeFormatDestroy(run_seq, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call NUOPC_DriverPrint(driver, orderflag=.true., rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
  end subroutine SetRunSequence

  !> A clear message on stdout (PET 0) and in the log, then an error return.
  subroutine fail(local_pet, msg, rc)
    integer, intent(in) :: local_pet
    character(len=*), intent(in) :: msg
    integer, intent(out) :: rc
    if (local_pet == 0) then
      print '(a)', msg
      flush (6)
    end if
    call ESMF_LogSetError(ESMF_RC_ARG_BAD, msg=msg, line=__LINE__, file=__FILE__, rcToReturn=rc)
  end subroutine fail

end module proto_driver
