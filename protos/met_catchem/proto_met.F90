!> MET: the analytic meteorology provider (NUOPC_Model).
!!
!! Exports every field CATChem's field mapping imports (read with the cap's own parser, so the two lists cannot
!! drift) plus the rank-4 tracer field with the tracerNames/tracerUnits Array metadata the cap reads, all with
!! "will provide" and share policies so the connector reference-shares them into the cap. It also hosts
!! CATChem's five exports as its own "will provide" imports: NUOPC never prunes a model's unconnected exports
!! and the cap fails if one is unrealized. Fields are filled from the manifest's `met` parameters and the grid
!! coordinates in DataInitialize and in every Advance; exports are stamped at step start (label_TimestampExport
!! override) so CATChem's default CheckImport passes with the sequence MET / MET -> CATCHEM / CATCHEM. The
!! tracer export persists CATChem's previous-step chemistry (copied back from the import side) and recomputes
!! sphum analytically; nothing is read from disk.
!!
!! Hosted imports (task 4): the driver's `met_hosted_imports::` rows name fields another component (CECE)
!! exports that MET consumes as its own "cannot provide"/"share" imports, so the connector realizes them with
!! the provider's allocation (NUOPC 2.4.7/2.4.8: a "will provide" import would be realized twice and never
!! shared). After every step MET sums field x cell area per level over all PETs and PET 0 rewrites
!! met_import_totals.yaml in the task-2 Totals layout; the stamp is the provider's export timestamp.
module proto_met
  use ESMF
  use NUOPC
  use NUOPC_Model, only: modelSS => SetServices, label_Advertise, label_RealizeProvided, label_DataInitialize, &
                         label_Advance, label_CheckImport, label_TimestampExport, label_Finalize, NUOPC_ModelGet
  use catchem_nuopc_interface, only: load_field_config, field_config, is_emission_import
  use proto_manifest
  use proto_met_analytic
  use proto_totals
  implicit none
  private

  public :: SetServices, proto_met_configure

  character(len=*), parameter :: STATE_LABEL = "proto_met_state"
  character(len=*), parameter :: TRACER_FIELD = "inst_tracer_mass_frac"
  character(len=*), parameter :: TOTALS_FILE = "met_import_totals.yaml"   !< tools.settings met_totals_filename
  integer, parameter :: TRACER_NAME_LEN = 32   !< the FV3 cap writes character(len=32) arrays

  character(len=ESMF_MAXPATHLEN), save :: cfg_manifest = ''
  character(len=ESMF_MAXPATHLEN), save :: cfg_data_root = '.'
  character(len=ESMF_MAXPATHLEN), save :: cfg_mapping = ''
  type(hosted_spec), allocatable, save :: cfg_hosted(:)

  type field_spec
    character(len=128) :: name = ''
    integer :: rank = 2
    character(len=32) :: axis = 'level'
  end type field_spec

  type met_state
    type(proto_manifest_type) :: m
    type(ESMF_Grid) :: grid
    integer :: nx = 0, ny = 0, nz = 0, nsoil = 0, ntr = 0, sphum = 0
    real(rk), allocatable :: lon(:, :), lat(:, :), area(:, :), land(:, :), oro(:, :)
    type(field_spec), allocatable :: exports(:), imports(:)
    type(ESMF_Time) :: step_start
    integer :: step = 0
    ! hosted imports and their totals: tot(level, record, hosted), one stamp per record
    type(ESMF_VM) :: vm
    integer :: local_pet = 0
    logical :: catchem_present = .false.   !< CATChem's exports are connected (its tracers are copied back)
    type(hosted_spec), allocatable :: hosted(:)
    real(rk), allocatable :: tot(:, :, :)
    character(len=STAMP_LEN), allocatable :: stamps(:)
    integer :: nrec = 0
    ! per-step work arrays (surface and column)
    real(rk), allocatable :: ts(:, :), ps(:, :), cosz(:, :)
    real(rk), allocatable :: pedge(:, :, :), pmid(:, :, :), temp(:, :, :), phie(:, :, :), phim(:, :, :)
    real(rk), allocatable :: u(:, :, :), v(:, :, :), cldf(:, :, :), q(:, :, :)
  end type met_state

  type met_wrap
    type(met_state), pointer :: p => null()
  end type met_wrap

contains

  !> Called by the driver before NUOPC_DriverAddComp, like cc_nuopc_set_config_file for the cap. `hosted`
  !! lists the imports MET hosts for another component's exports (may be empty).
  subroutine proto_met_configure(manifest, data_root, mapping_file, hosted)
    character(len=*), intent(in) :: manifest, data_root, mapping_file
    type(hosted_spec), intent(in) :: hosted(:)
    cfg_manifest = manifest
    cfg_data_root = data_root
    cfg_mapping = mapping_file
    cfg_hosted = hosted
  end subroutine proto_met_configure

  subroutine SetServices(model, rc)
    type(ESMF_GridComp) :: model
    integer, intent(out) :: rc
    type(met_wrap) :: wrap
    rc = ESMF_SUCCESS
    call NUOPC_CompDerive(model, modelSS, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call NUOPC_CompSpecialize(model, specLabel=label_Advertise, specRoutine=Advertise, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call NUOPC_CompSpecialize(model, specLabel=label_RealizeProvided, specRoutine=Realize, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call NUOPC_CompSpecialize(model, specLabel=label_DataInitialize, specRoutine=DataInitialize, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call NUOPC_CompSpecialize(model, specLabel=label_Advance, specRoutine=Advance, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    ! CATChem stamps its exports at step end and never marks them Updated at initialization; MET only
    ! hosts them (and copies the tracers back), so the default import timestamp check does not apply.
    call NUOPC_CompSpecialize(model, specLabel=label_CheckImport, specRoutine=CheckImport, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call NUOPC_CompSpecialize(model, specLabel=label_TimestampExport, specRoutine=TimestampExport, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call NUOPC_CompSpecialize(model, specLabel=label_Finalize, specRoutine=Finalize, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    allocate (wrap%p)
    call ESMF_UserCompSetInternalState(model, STATE_LABEL, wrap, rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
  end subroutine SetServices

  function state_of(model, rc) result(s)
    type(ESMF_GridComp) :: model
    integer, intent(out) :: rc
    type(met_state), pointer :: s
    type(met_wrap) :: wrap
    nullify (s)
    call ESMF_UserCompGetInternalState(model, STATE_LABEL, wrap, rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    s => wrap%p
  end function state_of

  ! --- initialize --------------------------------------------------------------------------------------------

  subroutine Advertise(model, rc)
    type(ESMF_GridComp) :: model
    integer, intent(out) :: rc
    type(met_state), pointer :: s
    type(ESMF_State) :: importState, exportState
    character(len=512) :: errmsg
    integer :: i, n
    rc = ESMF_SUCCESS
    s => state_of(model, rc); if (rc /= ESMF_SUCCESS) return
    call NUOPC_ModelGet(model, importState=importState, exportState=exportState, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return

    call manifest_read(trim(cfg_manifest), s%m, rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="MET: manifest "//trim(cfg_manifest), line=__LINE__, file=__FILE__)) return
    s%nz = s%m%n_levels
    s%nsoil = s%m%n_soil_layers
    s%ntr = size(s%m%tracers)
    s%sphum = 0
    do i = 1, s%ntr
      if (lower(trim(s%m%tracers(i)%name)) == 'sphum') s%sphum = i
    end do
    if (s%sphum == 0) then
      call ESMF_LogSetError(ESMF_RC_ARG_BAD, msg="MET: manifest met.tracers has no sphum", line=__LINE__, file=__FILE__, rcToReturn=rc)
      return
    end if

    ! The cap's own parser: MET exports exactly what the cap advertises (the cap deallocates the module
    ! variable in its DataInitialize, so the lists are copied here).
    call load_field_config(trim(cfg_mapping), rc, errmsg)
    if (rc /= 0) then
      call ESMF_LogSetError(ESMF_RC_FILE_READ, msg="MET: field mapping "//trim(cfg_mapping)//": "//trim(errmsg), &
                            line=__LINE__, file=__FILE__, rcToReturn=rc)
      return
    end if
    ! The emission entries (catchem_var extemis/<category>/<variable>) are CECE's exports, not meteorology.
    n = 0
    do i = 1, field_config%n_import_fields
      if (field_config%import_fields(i)%optional .and. .not. field_config%import_fields(i)%advertise) cycle
      if (is_emission_import(field_config%import_fields(i))) cycle
      n = n + 1
    end do
    allocate (s%exports(n), s%imports(field_config%n_export_fields))
    n = 0
    do i = 1, field_config%n_import_fields
      if (field_config%import_fields(i)%optional .and. .not. field_config%import_fields(i)%advertise) cycle
      if (is_emission_import(field_config%import_fields(i))) cycle
      n = n + 1
      s%exports(n)%name = trim(field_config%import_fields(i)%standard_name)
      s%exports(n)%rank = field_config%import_fields(i)%dimensions
      s%exports(n)%axis = trim(field_config%import_fields(i)%vertical_axis)
    end do
    do i = 1, field_config%n_export_fields
      s%imports(i)%name = trim(field_config%export_fields(i)%standard_name)
      s%imports(i)%rank = field_config%export_fields(i)%dimensions
      s%imports(i)%axis = trim(field_config%export_fields(i)%vertical_axis)
    end do

    do i = 1, size(s%exports)
      call NUOPC_Advertise(exportState, StandardName=trim(s%exports(i)%name), name=trim(s%exports(i)%name), &
                           TransferOfferGeomObject="will provide", SharePolicyField="share", SharePolicyGeomObject="share", rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg="MET: advertise export "//trim(s%exports(i)%name), line=__LINE__, file=__FILE__)) return
    end do
    do i = 1, size(s%imports)
      call NUOPC_Advertise(importState, StandardName=trim(s%imports(i)%name), name=trim(s%imports(i)%name), &
                           TransferOfferGeomObject="will provide", SharePolicyField="share", SharePolicyGeomObject="share", rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg="MET: advertise import "//trim(s%imports(i)%name), line=__LINE__, file=__FILE__)) return
    end do
    ! Hosted imports: the provider realizes ("will provide"), MET accepts ("cannot provide"), both share, so
    ! the connector hands MET the provider's allocation (NUOPC 2.4.7 table, 2.4.8).
    if (allocated(cfg_hosted)) then
      s%hosted = cfg_hosted
    else
      allocate (s%hosted(0))
    end if
    do i = 1, size(s%hosted)
      call NUOPC_Advertise(importState, StandardName=trim(s%hosted(i)%standard_name), name=trim(s%hosted(i)%standard_name), &
                           TransferOfferGeomObject="cannot provide", SharePolicyField="share", SharePolicyGeomObject="share", rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg="MET: advertise hosted import "//trim(s%hosted(i)%standard_name), line=__LINE__, file=__FILE__)) return
      call NUOPC_FieldDictionaryGetEntry(trim(s%hosted(i)%standard_name), canonicalUnits=s%hosted(i)%units, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg="MET: dictionary entry of "//trim(s%hosted(i)%standard_name), line=__LINE__, file=__FILE__)) return
    end do
    call ESMF_LogWrite("MET: advertised "//itoa(size(s%exports))//" exports, "//itoa(size(s%imports))// &
                       " imports (CATChem's exports) from "//trim(cfg_mapping)//" and "//itoa(size(s%hosted))//" hosted imports", ESMF_LOGMSG_INFO)
  end subroutine Advertise

  subroutine Realize(model, rc)
    type(ESMF_GridComp) :: model
    integer, intent(out) :: rc
    type(met_state), pointer :: s
    type(ESMF_State) :: importState, exportState
    type(ESMF_Field) :: field, area_field
    type(ESMF_VM) :: vm
    real(rk), pointer :: p2(:, :)
    real(rk) :: area_sum(1), area_total
    integer :: i, j, local_de_count, tile_count, n_kept
    rc = ESMF_SUCCESS
    s => state_of(model, rc); if (rc /= ESMF_SUCCESS) return
    call NUOPC_ModelGet(model, importState=importState, exportState=exportState, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call ESMF_GridCompGet(model, grid=s%grid, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="MET: the driver must set the grid on the component", line=__LINE__, file=__FILE__)) return
    call ESMF_GridGet(s%grid, localDECount=local_de_count, tileCount=tile_count, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    if (local_de_count /= 1) then
      call ESMF_LogSetError(ESMF_RC_ARG_BAD, msg="MET: expected one DE per PET, got "//itoa(local_de_count), &
                            line=__LINE__, file=__FILE__, rcToReturn=rc)
      return
    end if

    ! centre coordinates (degrees, from the mosaic reader) and the exact cell areas from the corners
    nullify (p2)
    call ESMF_GridGetCoord(s%grid, coordDim=1, localDE=0, staggerloc=ESMF_STAGGERLOC_CENTER, farrayPtr=p2, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    s%lon = p2
    nullify (p2)
    call ESMF_GridGetCoord(s%grid, coordDim=2, localDE=0, staggerloc=ESMF_STAGGERLOC_CENTER, farrayPtr=p2, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    s%lat = p2
    s%nx = size(s%lon, 1)
    s%ny = size(s%lon, 2)
    area_field = ESMF_FieldCreate(s%grid, typekind=ESMF_TYPEKIND_R8, staggerloc=ESMF_STAGGERLOC_CENTER, name="area", rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call ESMF_FieldRegridGetArea(area_field, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg="MET: ESMF_FieldRegridGetArea on the mosaic grid", line=__LINE__, file=__FILE__)) return
    nullify (p2)
    call ESMF_FieldGet(area_field, farrayPtr=p2, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    ! ESMF_FieldRegridGetArea returns square radians on a sphere (ESMF_refdoc 27.6.73); the mapping wants m2.
    s%area = p2 * s%m%earth_radius_m**2
    call ESMF_FieldDestroy(area_field, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    ! The six tiles must cover the sphere: a check of the grid's corner coordinates, logged on every PET.
    call ESMF_GridCompGet(model, vm=vm, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    s%vm = vm
    call ESMF_VMGet(vm, localPet=s%local_pet, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    area_sum(1) = sum(s%area)
    call ESMF_VMAllFullReduce(vm, area_sum, area_total, 1, ESMF_REDUCE_SUM, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call ESMF_LogWrite("MET: global cell area / (4 pi R^2) = "// &
                       ftoa(area_total / (4.0_rk * PI * s%m%earth_radius_m**2)), ESMF_LOGMSG_INFO)

    allocate (s%land(s%nx, s%ny), s%oro(s%nx, s%ny))
    do j = 1, s%ny
      do i = 1, s%nx
        s%land(i, j) = land_fraction(s%m, s%lon(i, j), s%lat(i, j))
        s%oro(i, j) = orography(s%m, s%land(i, j), s%lat(i, j))
      end do
    end do
    allocate (s%ts(s%nx, s%ny), s%ps(s%nx, s%ny), s%cosz(s%nx, s%ny))
    allocate (s%pedge(s%nx, s%ny, s%nz + 1), s%phie(s%nx, s%ny, s%nz + 1))
    allocate (s%pmid(s%nx, s%ny, s%nz), s%temp(s%nx, s%ny, s%nz), s%phim(s%nx, s%ny, s%nz), &
              s%u(s%nx, s%ny, s%nz), s%v(s%nx, s%ny, s%nz), s%cldf(s%nx, s%ny, s%nz), s%q(s%nx, s%ny, s%nz))

    do i = 1, size(s%exports)
      field = create_field(s, s%exports(i), rc); if (rc /= ESMF_SUCCESS) return
      call NUOPC_Realize(exportState, field=field, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg="MET: realize export "//trim(s%exports(i)%name), line=__LINE__, file=__FILE__)) return
    end do
    ! CATChem's exports are hosted only when CATChem runs: an unconnected one (no CATCHEM in the component
    ! list) is removed from the state, like CECE removes its unconnected exports, or NUOPC_ModelBase stops
    ! with "Import Fields not all connected". A hosted import of another provider that is not connected is a
    ! configuration error, not a removal.
    n_kept = 0
    do i = 1, size(s%imports)
      if (.not. NUOPC_IsConnected(importState, fieldName=trim(s%imports(i)%name), rc=rc)) then
        if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
        call ESMF_StateRemove(importState, (/trim(s%imports(i)%name)/), rc=rc)
        if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
        call ESMF_LogWrite("MET: removed unconnected hosted CATChem export "//trim(s%imports(i)%name), ESMF_LOGMSG_INFO)
        cycle
      end if
      n_kept = n_kept + 1
      field = create_field(s, s%imports(i), rc); if (rc /= ESMF_SUCCESS) return
      call NUOPC_Realize(importState, field=field, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg="MET: realize import "//trim(s%imports(i)%name), line=__LINE__, file=__FILE__)) return
    end do
    s%catchem_present = n_kept > 0
    do i = 1, size(s%hosted)
      if (.not. NUOPC_IsConnected(importState, fieldName=trim(s%hosted(i)%standard_name), rc=rc)) then
        if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
        call ESMF_LogSetError(ESMF_RC_ARG_BAD, msg="MET: hosted import "//trim(s%hosted(i)%standard_name)// &
                              " is not connected: no component exports it (check the CECE nuopc.export_fields and the run sequence)", &
                              line=__LINE__, file=__FILE__, rcToReturn=rc)
        return
      end if
    end do
    call ESMF_LogWrite("MET: realized "//itoa(size(s%exports))//" exports and "//itoa(size(s%imports))// &
                       " imports on a "//itoa(tile_count)//"-tile grid, local DE "//itoa(s%nx)//"x"//itoa(s%ny)// &
                       ", nz="//itoa(s%nz)//", nsoil="//itoa(s%nsoil)//", ntracers="//itoa(s%ntr), ESMF_LOGMSG_INFO)
  end subroutine Realize

  !> One field of the mapping's shape: 2-D at centre, 3-D with the axis' extent, rank 4 with the tracer metadata.
  function create_field(s, spec, rc) result(field)
    type(met_state), intent(in) :: s
    type(field_spec), intent(in) :: spec
    integer, intent(out) :: rc
    type(ESMF_Field) :: field
    type(ESMF_Array) :: array
    type(ESMF_Info) :: info
    character(len=TRACER_NAME_LEN), allocatable :: names(:), units(:)
    real(rk), pointer :: p2(:, :), p3(:, :, :), p4(:, :, :, :)
    integer :: nlev, i
    rc = ESMF_SUCCESS
    select case (spec%rank)
    case (2)
      field = ESMF_FieldCreate(s%grid, typekind=ESMF_TYPEKIND_R8, staggerloc=ESMF_STAGGERLOC_CENTER, name=trim(spec%name), rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      nullify (p2); call ESMF_FieldGet(field, farrayPtr=p2, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      p2 = 0.0_rk
    case (3)
      select case (trim(spec%axis))
      case ('interface')
        nlev = s%nz + 1
      case ('soil_layer')
        nlev = s%nsoil
      case default   ! 'level' and 'level_to_interface': the host supplies nz
        nlev = s%nz
      end select
      field = ESMF_FieldCreate(s%grid, typekind=ESMF_TYPEKIND_R8, staggerloc=ESMF_STAGGERLOC_CENTER, &
                               ungriddedLBound=(/1/), ungriddedUBound=(/nlev/), name=trim(spec%name), rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      nullify (p3); call ESMF_FieldGet(field, farrayPtr=p3, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      p3 = 0.0_rk
    case (4)
      field = ESMF_FieldCreate(s%grid, typekind=ESMF_TYPEKIND_R8, staggerloc=ESMF_STAGGERLOC_CENTER, &
                               ungriddedLBound=(/1, 1/), ungriddedUBound=(/s%nz, s%ntr/), name=trim(spec%name), rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      nullify (p4); call ESMF_FieldGet(field, farrayPtr=p4, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      p4 = 0.0_rk
      ! The two keys the CATChem cap (and GOCART) read from the Array's Info, as the FV3 cap sets them.
      allocate (names(s%ntr), units(s%ntr))
      do i = 1, s%ntr
        names(i) = s%m%tracers(i)%name
        units(i) = s%m%tracers(i)%units
      end do
      call ESMF_FieldGet(field, array=array, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      call ESMF_InfoGetFromHost(array, info, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      call ESMF_InfoSet(info, "tracerNames", names, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      call ESMF_InfoSet(info, "tracerUnits", units, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    case default
      call ESMF_LogSetError(ESMF_RC_ARG_BAD, msg="MET: unsupported rank for "//trim(spec%name), line=__LINE__, file=__FILE__, rcToReturn=rc)
    end select
  end function create_field

  subroutine DataInitialize(model, rc)
    type(ESMF_GridComp) :: model
    integer, intent(out) :: rc
    type(met_state), pointer :: s
    type(ESMF_State) :: importState, exportState
    type(ESMF_Clock) :: clock
    type(ESMF_Time) :: now
    type(ESMF_Field) :: field
    real(rk), pointer :: p3(:, :, :)
    character(len=64) :: share_field, share_geom
    real(ESMF_KIND_R8) :: n_steps   !< ESMF_ClockGet runTimeStepCount is a real
    integer :: i, n_shared, rank, max_lev
    rc = ESMF_SUCCESS
    s => state_of(model, rc); if (rc /= ESMF_SUCCESS) return
    call NUOPC_ModelGet(model, modelClock=clock, importState=importState, exportState=exportState, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call ESMF_ClockGet(clock, currTime=now, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    s%step_start = now
    call fill_exports(s, exportState, now, rc); if (rc /= ESMF_SUCCESS) return
    ! Hosted imports: realized by the connector with the provider's storage by now; the shared status is the
    ! proof, anything else is an error (a copy would hide a decomposition mismatch).
    max_lev = 1
    do i = 1, size(s%hosted)
      call ESMF_StateGet(importState, itemName=trim(s%hosted(i)%standard_name), field=field, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg="MET: hosted import "//trim(s%hosted(i)%standard_name)//" missing", line=__LINE__, file=__FILE__)) return
      call NUOPC_GetAttribute(field, name="ShareStatusField", value=share_field, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      call NUOPC_GetAttribute(field, name="ShareStatusGeomObject", value=share_geom, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      call ESMF_FieldGet(field, rank=rank, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      select case (rank)
      case (2)
        s%hosted(i)%nlev = 1
      case (3)
        nullify (p3); call ESMF_FieldGet(field, farrayPtr=p3, rc=rc)
        if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
        s%hosted(i)%nlev = size(p3, 3)
      case default
        call ESMF_LogSetError(ESMF_RC_ARG_BAD, msg="MET: hosted import "//trim(s%hosted(i)%standard_name)//" has rank "//itoa(rank)// &
                              "; 2 or 3 expected", line=__LINE__, file=__FILE__, rcToReturn=rc)
        return
      end select
      max_lev = max(max_lev, s%hosted(i)%nlev)
      call ESMF_LogWrite("MET: hosted import "//trim(s%hosted(i)%standard_name)//" ShareStatusField="//trim(share_field)// &
                         " ShareStatusGeomObject="//trim(share_geom)//" rank="//itoa(rank)//" levels="//itoa(s%hosted(i)%nlev)// &
                         " units="//trim(s%hosted(i)%units), ESMF_LOGMSG_INFO)
      if (trim(share_field) /= "shared") then
        call ESMF_LogSetError(ESMF_RC_ARG_BAD, msg="MET: hosted import "//trim(s%hosted(i)%standard_name)// &
                              " is not reference-shared (ShareStatusField="//trim(share_field)//")", line=__LINE__, file=__FILE__, rcToReturn=rc)
        return
      end if
    end do
    if (size(s%hosted) > 0) then
      call ESMF_ClockGet(clock, runTimeStepCount=n_steps, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      allocate (s%tot(max_lev, nint(n_steps) + 1, size(s%hosted)), s%stamps(nint(n_steps) + 1))
      s%tot = 0.0_rk
      s%nrec = 0
    end if
    ! The connectors have negotiated sharing by now: report it (the shared status is the proof that the cap
    ! received MET's allocations, not copies).
    n_shared = 0
    do i = 1, size(s%exports)
      call ESMF_StateGet(exportState, itemName=trim(s%exports(i)%name), field=field, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      call NUOPC_SetAttribute(field, name="Updated", value="true", rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      call NUOPC_GetAttribute(field, name="ShareStatusField", value=share_field, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      call NUOPC_GetAttribute(field, name="ShareStatusGeomObject", value=share_geom, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      if (trim(share_field) == "shared") n_shared = n_shared + 1
      call ESMF_LogWrite("MET: export "//trim(s%exports(i)%name)//" ShareStatusField="//trim(share_field)// &
                         " ShareStatusGeomObject="//trim(share_geom), ESMF_LOGMSG_INFO)
    end do
    call ESMF_LogWrite("MET: "//itoa(n_shared)//" of "//itoa(size(s%exports))//" exports are reference-shared", ESMF_LOGMSG_INFO)
    call NUOPC_CompAttributeSet(model, name="InitializeDataComplete", value="true", rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
  end subroutine DataInitialize

  ! --- run ---------------------------------------------------------------------------------------------------

  subroutine CheckImport(model, rc)
    type(ESMF_GridComp) :: model
    integer, intent(out) :: rc
    associate (unused => model); end associate
    rc = ESMF_SUCCESS
  end subroutine CheckImport

  subroutine Advance(model, rc)
    type(ESMF_GridComp) :: model
    integer, intent(out) :: rc
    type(met_state), pointer :: s
    type(ESMF_State) :: importState, exportState
    type(ESMF_Clock) :: clock
    type(ESMF_Time) :: now
    type(ESMF_Field) :: fin, fout
    real(rk), pointer :: tin(:, :, :, :), tout(:, :, :, :)
    character(len=32) :: stamp
    integer :: j
    rc = ESMF_SUCCESS
    s => state_of(model, rc); if (rc /= ESMF_SUCCESS) return
    call NUOPC_ModelGet(model, modelClock=clock, importState=importState, exportState=exportState, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call ESMF_ClockGet(clock, currTime=now, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    s%step_start = now
    ! Persist CATChem's chemistry: its previous-step tracer export (MET's import) becomes this step's tracer
    ! import for every slot but sphum, which fill_exports recomputes. Without CATChem the tracers stay as
    ! DataInitialize left them.
    if (s%catchem_present) then
      call ESMF_StateGet(importState, itemName=TRACER_FIELD, field=fin, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      call ESMF_StateGet(exportState, itemName=TRACER_FIELD, field=fout, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      nullify (tin, tout)
      call ESMF_FieldGet(fin, farrayPtr=tin, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      call ESMF_FieldGet(fout, farrayPtr=tout, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      do j = 1, s%ntr
        if (j /= s%sphum) tout(:, :, :, j) = tin(:, :, :, j)
      end do
    end if
    call fill_exports(s, exportState, now, rc); if (rc /= ESMF_SUCCESS) return
    call record_hosted(s, importState, now, rc); if (rc /= ESMF_SUCCESS) return
    if (s%catchem_present) then
      call log_tracer_sums(s, tout, now, rc); if (rc /= ESMF_SUCCESS) return
    end if
    s%step = s%step + 1
    if (s%step == 1 .or. mod(s%step, 10) == 0) then
      call ESMF_TimeGet(now, timeString=stamp, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      if (s%catchem_present) then
        call ESMF_LogWrite("MET: step "//itoa(s%step)//" at "//trim(stamp)//": Ts(1,1)="//ftoa(s%ts(1, 1))// &
                           " K, ps(1,1)="//ftoa(s%ps(1, 1))//" Pa, seas1 max="//ftoa(maxval(tout(:, :, :, min(2, s%ntr)))), ESMF_LOGMSG_INFO)
      else
        call ESMF_LogWrite("MET: step "//itoa(s%step)//" at "//trim(stamp)//": Ts(1,1)="//ftoa(s%ts(1, 1))// &
                           " K, ps(1,1)="//ftoa(s%ps(1, 1))//" Pa", ESMF_LOGMSG_INFO)
      end if
    end if
  end subroutine Advance

  !> On the hour, the global area-weighted sum over all levels of every tracer slot but sphum as CATChem
  !! left it (sum of q x cell area, ESMF_VMAllFullReduce per tracer), one line per tracer on every PET: the evidence
  !! that emissions accumulate in the host's tracer field (task 6 checks so2 growing from zero).
  subroutine log_tracer_sums(s, q, now, rc)
    type(met_state), intent(inout) :: s
    real(rk), pointer, intent(in) :: q(:, :, :, :)
    type(ESMF_Time), intent(in) :: now
    integer, intent(out) :: rc
    real(rk) :: local_sum(1), total
    character(len=32) :: stamp
    integer :: j, k
    rc = ESMF_SUCCESS
    call ESMF_TimeGet(now, timeString=stamp, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    ! on the hour only: the ISO string's mm:ss (YYYY-MM-DDThh:mm:ss)
    if (stamp(15:19) /= "00:00") return
    do j = 1, s%ntr
      if (j == s%sphum) cycle
      local_sum(1) = 0.0_rk
      do k = lbound(q, 3), ubound(q, 3)
        local_sum(1) = local_sum(1) + sum(q(:, :, k, j) * s%area)
      end do
      call ESMF_VMAllFullReduce(s%vm, local_sum, total, 1, ESMF_REDUCE_SUM, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      call ESMF_LogWrite("MET: tracer "//trim(s%m%tracers(j)%name)//" area-weighted sum "//ftoa(total)// &
                         " at "//trim(stamp), ESMF_LOGMSG_INFO)
    end do
  end subroutine log_tracer_sums

  !> One record per step for every hosted import: the global sum of field x cell area per level
  !! (ESMF_VMAllFullReduce), stamped with the provider's export timestamp (CECE stamps at step start) and
  !! rewritten by PET 0 as met_import_totals.yaml in the run directory.
  subroutine record_hosted(s, importState, now, rc)
    type(met_state), intent(inout) :: s
    type(ESMF_State), intent(inout) :: importState
    type(ESMF_Time), intent(in) :: now
    integer, intent(out) :: rc
    type(ESMF_Field) :: field
    type(ESMF_Time) :: stamp_time
    real(rk), pointer :: p2(:, :), p3(:, :, :)
    real(rk) :: local_sum(1), total
    logical :: valid
    character(len=32) :: stamp
    character(len=512) :: line
    integer :: h, k, rank
    rc = ESMF_SUCCESS
    if (size(s%hosted) == 0) return
    if (s%nrec >= size(s%stamps)) then
      call ESMF_LogSetError(ESMF_RC_ARG_BAD, msg="MET: more steps than the clock announced; totals table full", &
                            line=__LINE__, file=__FILE__, rcToReturn=rc)
      return
    end if
    s%nrec = s%nrec + 1
    line = "MET: totals"
    do h = 1, size(s%hosted)
      call ESMF_StateGet(importState, itemName=trim(s%hosted(h)%standard_name), field=field, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      if (h == 1) then
        call NUOPC_GetTimestamp(field, isValid=valid, time=stamp_time, rc=rc)
        if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
        if (.not. valid) stamp_time = now
        call ESMF_TimeGet(stamp_time, timeString=stamp, rc=rc)
        if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
        s%stamps(s%nrec) = trim(stamp)//"Z"
        line = trim(line)//" "//trim(stamp)
      end if
      call ESMF_FieldGet(field, rank=rank, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
      do k = 1, s%hosted(h)%nlev
        if (rank == 2) then
          nullify (p2); call ESMF_FieldGet(field, farrayPtr=p2, rc=rc)
          if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
          local_sum(1) = sum(p2 * s%area)
        else
          nullify (p3); call ESMF_FieldGet(field, farrayPtr=p3, rc=rc)
          if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
          local_sum(1) = sum(p3(:, :, k) * s%area)
        end if
        call ESMF_VMAllFullReduce(s%vm, local_sum, total, 1, ESMF_REDUCE_SUM, rc=rc)
        if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
        s%tot(k, s%nrec, h) = total
      end do
      line = trim(line)//" "//trim(s%hosted(h)%category)//"/"//trim(s%hosted(h)%variable)//" = "//ftoa(s%tot(1, s%nrec, h))
    end do
    call ESMF_LogWrite(trim(line), ESMF_LOGMSG_INFO)
    if (s%local_pet == 0) then
      call proto_totals_write(TOTALS_FILE, s%hosted, s%nrec, s%stamps, s%tot, rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg="MET: writing "//TOTALS_FILE, line=__LINE__, file=__FILE__)) return
    end if
  end subroutine record_hosted

  !> Exports carry the step-start stamp (CECE's convention) so CATChem's default CheckImport, which runs
  !! after MET in the same step, finds them at its current time.
  subroutine TimestampExport(model, rc)
    type(ESMF_GridComp) :: model
    integer, intent(out) :: rc
    type(met_state), pointer :: s
    type(ESMF_State) :: exportState
    rc = ESMF_SUCCESS
    s => state_of(model, rc); if (rc /= ESMF_SUCCESS) return
    call NUOPC_ModelGet(model, exportState=exportState, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    call NUOPC_SetTimestamp(exportState, s%step_start, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
  end subroutine TimestampExport

  subroutine Finalize(model, rc)
    type(ESMF_GridComp) :: model
    integer, intent(out) :: rc
    type(met_state), pointer :: s
    rc = ESMF_SUCCESS
    s => state_of(model, rc); if (rc /= ESMF_SUCCESS) return
    call ESMF_LogWrite("MET: finalize after "//itoa(s%step)//" steps", ESMF_LOGMSG_INFO)
    deallocate (s)
  end subroutine Finalize

  ! --- the analytic fill -------------------------------------------------------------------------------------

  subroutine fill_exports(s, exportState, time, rc)
    type(met_state), intent(inout) :: s
    type(ESMF_State), intent(inout) :: exportState
    type(ESMF_Time), intent(in) :: time
    integer, intent(out) :: rc
    type(ESMF_Field) :: field
    real(rk), pointer :: p2(:, :), p3(:, :, :), p4(:, :, :, :)
    real(rk) :: hour_utc, hloc, hour
    integer :: doy, hh, mm, ss, i, j, k, n

    rc = ESMF_SUCCESS
    call ESMF_TimeGet(time, dayOfYear=doy, h=hh, m=mm, s=ss, rc=rc)
    if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
    hour_utc = real(hh, rk) + real(mm, rk) / 60.0_rk + real(ss, rk) / 3600.0_rk
    hour = hour_utc   ! keeps the formulas below explicit about which hour they use

    do j = 1, s%ny
      do i = 1, s%nx
        hloc = local_solar_hour(hour, s%lon(i, j))
        s%cosz(i, j) = cos_zenith(s%lat(i, j), hloc, doy)
        s%ts(i, j) = surface_temperature(s%m, s%lat(i, j), hloc)
        s%ps(i, j) = surface_pressure(s%m, s%oro(i, j))
        call column(s%m, s%nz, s%ps(i, j), s%ts(i, j), s%oro(i, j), s%lon(i, j), s%lat(i, j), &
                    s%pedge(i, j, :), s%pmid(i, j, :), s%temp(i, j, :), s%phie(i, j, :), s%phim(i, j, :), &
                    s%u(i, j, :), s%v(i, j, :), s%cldf(i, j, :), s%q(i, j, :))
      end do
    end do

    do n = 1, size(s%exports)
      call ESMF_StateGet(exportState, itemName=trim(s%exports(n)%name), field=field, rc=rc)
      if (ESMF_LogFoundError(rcToCheck=rc, msg="MET: export "//trim(s%exports(n)%name)//" missing", line=__LINE__, file=__FILE__)) return
      select case (s%exports(n)%rank)
      case (2)
        nullify (p2); call ESMF_FieldGet(field, farrayPtr=p2, rc=rc)
        if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
        call fill_2d(s, trim(s%exports(n)%name), p2, rc); if (rc /= ESMF_SUCCESS) return
      case (3)
        nullify (p3); call ESMF_FieldGet(field, farrayPtr=p3, rc=rc)
        if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
        call fill_3d(s, trim(s%exports(n)%name), p3, rc); if (rc /= ESMF_SUCCESS) return
      case (4)
        nullify (p4); call ESMF_FieldGet(field, farrayPtr=p4, rc=rc)
        if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, line=__LINE__, file=__FILE__)) return
        if (trim(s%exports(n)%name) /= TRACER_FIELD) then
          call ESMF_LogSetError(ESMF_RC_ARG_BAD, msg="MET: no analytic recipe for rank-4 export "//trim(s%exports(n)%name), &
                                line=__LINE__, file=__FILE__, rcToReturn=rc)
          return
        end if
        do k = 1, s%nz
          p4(:, :, k, s%sphum) = s%q(:, :, k)
        end do
      end select
    end do
  end subroutine fill_exports

  subroutine fill_2d(s, name, p, rc)
    type(met_state), intent(in) :: s
    character(len=*), intent(in) :: name
    real(rk), intent(inout) :: p(:, :)
    integer, intent(out) :: rc
    real(rk) :: day(s%nx, s%ny)
    logical :: is_land(s%nx, s%ny)
    rc = ESMF_SUCCESS
    day = max(s%cosz, 0.0_rk)
    is_land = s%land >= s%m%land_threshold
    select case (name)
    case ('inst_rainfall_amount', 'inst_convective_rainfall_amount', 'canopy_moisture_storage', 'lake_fraction')
      p = 0.0_rk
    case ('inst_zonal_wind_height10m')
      p = 0.6_rk * s%u(:, :, 1)
    case ('inst_merid_wind_height10m')
      p = 0.6_rk * s%v(:, :, 1)
    case ('inst_friction_velocity')
      p = s%m%friction_velocity_land_m_s * s%land + s%m%friction_velocity_ocean_m_s * (1.0_rk - s%land)
    case ('inst_land_sea_mask')
      p = merge(1.0_rk, 0.0_rk, is_land)
    case ('inst_temp_height_surface')
      p = s%ts
    case ('inst_pres_height_surface')
      p = s%ps
    case ('inst_temp_height2m')
      p = s%ts - 1.0_rk
    case ('inst_spec_humid_height2m')
      p = s%m%specific_humidity_surface
    case ('inst_sensi_heat_flx', 'inst_up_sensi_heat_flx')
      p = s%m%sensible_heat_peak_w_m2 * day * s%land
    case ('inst_laten_heat_flx')
      p = s%m%latent_heat_peak_w_m2 * day
    case ('inst_net_sw_flx')
      p = s%m%shortwave_peak_w_m2 * day
    case ('inst_aerodynamic_conductance')
      p = s%m%aerodynamic_conductance_s_m
    case ('inst_surface_roughness')   ! cm: the CATChem cap multiplies by 0.01
      p = s%m%roughness_length_land_cm * s%land + s%m%roughness_length_ocean_cm * (1.0_rk - s%land)
    case ('inst_pbl_height')
      p = s%m%pbl_height_m
    case ('height')
      p = s%oro
    case ('inst_canopy_resistance')
      p = s%m%canopy_resistance_s_m
    case ('surface_cell_area')
      p = s%area
    case ('surface_snow_area_fraction')
      p = merge(s%land, 0.0_rk, abs(s%lat) >= s%m%snow_latitude_deg)
    case ('ice_fraction_in_atm', 'sea_ice_area_fraction')
      p = merge(1.0_rk - s%land, 0.0_rk, abs(s%lat) >= s%m%sea_ice_latitude_deg)
    case ('leaf_area_index')
      p = s%m%leaf_area_index * s%land
    case ('inst_vegetation_area_frac')
      p = s%m%vegetation_fraction * s%land
    case ('ocean_fraction')
      p = 1.0_rk - s%land
    case ('vegetation_type')
      p = merge(real(s%m%land_use_class, rk), 0.0_rk, is_land)
    case ('soil_type')
      p = merge(real(s%m%soil_type_class, rk), 0.0_rk, is_land)
    case ('inst_surface_soil_wetness')
      p = s%m%soil_moisture
    case default
      call ESMF_LogSetError(ESMF_RC_ARG_BAD, msg="MET: no analytic recipe for 2-D export "//name, line=__LINE__, file=__FILE__, rcToReturn=rc)
    end select
  end subroutine fill_2d

  subroutine fill_3d(s, name, p, rc)
    type(met_state), intent(in) :: s
    character(len=*), intent(in) :: name
    real(rk), intent(inout) :: p(:, :, :)
    integer, intent(out) :: rc
    integer :: k
    rc = ESMF_SUCCESS
    select case (name)
    case ('inst_temp_levels')
      p = s%temp
    case ('inst_geop_interface')   ! m2 s-2: the CATChem cap divides by g0
      p = s%phie
    case ('inst_geop_levels')
      p = s%phim
    case ('inst_pres_interface')
      p = s%pedge
    case ('inst_pres_levels')
      p = s%pmid
    case ('inst_zonal_wind_levels')
      p = s%u
    case ('inst_merid_wind_levels')
      p = s%v
    case ('inst_cloud_frac_levels')
      p = s%cldf
    case ('inst_ice_nonconv_tendency_levels', 'inst_liq_nonconv_tendency_levels')
      p = 0.0_rk
    case ('inst_soil_moisture_content')
      p = s%m%soil_moisture
    case ('temperature_of_soil_layer')
      do k = 1, size(p, 3)
        p(:, :, k) = s%ts - 2.0_rk
      end do
    case default
      call ESMF_LogSetError(ESMF_RC_ARG_BAD, msg="MET: no analytic recipe for 3-D export "//name, line=__LINE__, file=__FILE__, rcToReturn=rc)
    end select
  end subroutine fill_3d

  ! --- helpers -----------------------------------------------------------------------------------------------

  pure function lower(text) result(out)
    character(len=*), intent(in) :: text
    character(len=len(text)) :: out
    integer :: i, c
    out = text
    do i = 1, len(text)
      c = iachar(text(i:i))
      if (c >= iachar('A') .and. c <= iachar('Z')) out(i:i) = achar(c + 32)
    end do
  end function lower

  pure function itoa(i) result(text)
    integer, intent(in) :: i
    character(len=:), allocatable :: text
    character(len=16) :: buffer
    write (buffer, '(i0)') i
    text = trim(buffer)
  end function itoa

  pure function ftoa(x) result(text)
    real(rk), intent(in) :: x
    character(len=:), allocatable :: text
    character(len=24) :: buffer
    write (buffer, '(es12.4)') x
    text = trim(adjustl(buffer))
  end function ftoa

end module proto_met
