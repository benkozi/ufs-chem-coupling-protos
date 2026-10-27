!> met_catchem: MET and CATChem under the prototype driver.
!!
!!   esmApp [esm.configure]              run (the config defaults to ./esm.configure)
!!   esmApp --dump-manifest <manifest>   print every manifest value the components read, then exit
!!
!! The field dictionary named in the config is loaded before the driver exists; on success PET 0 prints
!! "met_catchem: completed <n> steps to <stop time>", on failure the error and an abort.
program esmApp
  use ESMF
  use NUOPC
  use proto_driver, only: driver_SS => SetServices
  use proto_manifest, only: proto_manifest_type, manifest_read, manifest_print
  implicit none

  type(ESMF_GridComp) :: driver
  type(ESMF_Config) :: config
  type(ESMF_Clock) :: clock
  type(ESMF_Time) :: now
  type(ESMF_VM) :: vm
  type(proto_manifest_type) :: m
  character(len=ESMF_MAXPATHLEN) :: config_file, dictionary, arg
  character(len=32) :: stamp
  integer(ESMF_KIND_I8) :: steps
  integer :: rc, user_rc, local_pet

  call ESMF_Initialize(defaultCalKind=ESMF_CALKIND_GREGORIAN, logkindflag=ESMF_LOGKIND_MULTI, vm=vm, rc=rc)
  if (rc /= ESMF_SUCCESS) error stop "met_catchem: ESMF_Initialize failed"
  call ESMF_VMGet(vm, localPet=local_pet, rc=rc)
  call check(rc, "ESMF_VMGet")

  config_file = "esm.configure"
  if (command_argument_count() >= 1) then
    call get_command_argument(1, arg)
    if (trim(arg) == "--dump-manifest") then
      if (command_argument_count() < 2) call abort_with("usage: esmApp --dump-manifest <manifest.yaml>")
      call get_command_argument(2, arg)
      call manifest_read(trim(arg), m, rc)
      call check(rc, "reading manifest "//trim(arg))
      if (local_pet == 0) call manifest_print(m, 6)
      call ESMF_Finalize(rc=rc)
      stop
    end if
    config_file = arg
  end if

  config = ESMF_ConfigCreate(rc=rc)
  call check(rc, "ESMF_ConfigCreate")
  call ESMF_ConfigLoadFile(config, trim(config_file), rc=rc)
  call check(rc, "loading driver config "//trim(config_file))
  call ESMF_ConfigGetAttribute(config, dictionary, label="field_dictionary:", rc=rc)
  call check(rc, "config key 'field_dictionary:' in "//trim(config_file))
  call NUOPC_FieldDictionarySetup(trim(dictionary), rc=rc)
  call check(rc, "NUOPC_FieldDictionarySetup "//trim(dictionary))

  driver = ESMF_GridCompCreate(name="met_catchem", config=config, rc=rc)
  call check(rc, "ESMF_GridCompCreate")
  call ESMF_GridCompSetServices(driver, driver_SS, userRc=user_rc, rc=rc)
  call check(rc, "SetServices"); call check(user_rc, "SetServices (user)")
  call ESMF_GridCompInitialize(driver, userRc=user_rc, rc=rc)
  call check(rc, "Initialize"); call check(user_rc, "Initialize (user)")
  call ESMF_GridCompRun(driver, userRc=user_rc, rc=rc)
  call check(rc, "Run"); call check(user_rc, "Run (user)")
  call ESMF_GridCompGet(driver, clock=clock, rc=rc)
  call check(rc, "ESMF_GridCompGet clock")
  call ESMF_ClockGet(clock, advanceCount=steps, currTime=now, rc=rc)
  call check(rc, "ESMF_ClockGet")
  call ESMF_TimeGet(now, timeString=stamp, rc=rc)
  call check(rc, "ESMF_TimeGet")
  call ESMF_GridCompFinalize(driver, userRc=user_rc, rc=rc)
  call check(rc, "Finalize"); call check(user_rc, "Finalize (user)")
  call ESMF_GridCompDestroy(driver, rc=rc)
  call check(rc, "ESMF_GridCompDestroy")
  if (local_pet == 0) print '(a,i0,a,a)', "met_catchem: completed ", steps, " steps to ", trim(stamp)
  call ESMF_Finalize(rc=rc)

contains

  subroutine check(code, what)
    integer, intent(in) :: code
    character(len=*), intent(in) :: what
    if (code == ESMF_SUCCESS) return
    call abort_with("met_catchem: "//what//" failed (see PET*.ESMF_LogFile)")
  end subroutine check

  subroutine abort_with(msg)
    character(len=*), intent(in) :: msg
    integer :: ignore
    print '(a)', msg
    flush(6)
    call ESMF_LogWrite(msg, ESMF_LOGMSG_ERROR, rc=ignore)
    call ESMF_Finalize(endflag=ESMF_END_ABORT, rc=ignore)
    error stop 1
  end subroutine abort_with

end program esmApp
