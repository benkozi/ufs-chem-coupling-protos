!> esmApp: the prototype driver's program (met_catchem, cece_met: the config's `prototype:` names it).
!!
!!   esmApp [esm.configure]              run (the config defaults to ./esm.configure)
!!   esmApp --dump-manifest <manifest>   print every manifest value the components read, then exit
!!
!! The field dictionary named in the config is loaded before the driver exists; on success PET 0 prints
!! "<prototype>: completed <n> steps to <stop time>", on failure the error and an abort.
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
  character(len=64) :: name = "met_catchem"
  character(len=32) :: stamp
  character(len=160) :: summary
  integer(ESMF_KIND_I8) :: steps
  integer :: rc, user_rc, local_pet

  call ESMF_Initialize(defaultCalKind=ESMF_CALKIND_GREGORIAN, logkindflag=ESMF_LOGKIND_MULTI, vm=vm, rc=rc)
  if (rc /= ESMF_SUCCESS) error stop "met_catchem: ESMF_Initialize failed"
  call ESMF_VMGet(vm, localPet=local_pet, rc=rc)
  call check(rc, "ESMF_VMGet", __LINE__)

  config_file = "esm.configure"
  if (command_argument_count() >= 1) then
    call get_command_argument(1, arg)
    if (trim(arg) == "--dump-manifest") then
      if (command_argument_count() < 2) call abort_with("usage: esmApp --dump-manifest <manifest.yaml>")
      call get_command_argument(2, arg)
      call manifest_read(trim(arg), m, rc)
      call check(rc, "reading manifest "//trim(arg), __LINE__)
      if (local_pet == 0) call manifest_print(m, 6)
      call ESMF_Finalize(rc=rc)
      stop
    end if
    config_file = arg
  end if

  config = ESMF_ConfigCreate(rc=rc)
  call check(rc, "ESMF_ConfigCreate", __LINE__)
  call ESMF_ConfigLoadFile(config, trim(config_file), rc=rc)
  call check(rc, "loading driver config "//trim(config_file), __LINE__)
  call ESMF_ConfigGetAttribute(config, name, label="prototype:", default="met_catchem", rc=rc)
  call check(rc, "config key 'prototype:' in "//trim(config_file), __LINE__)
  call ESMF_ConfigGetAttribute(config, dictionary, label="field_dictionary:", rc=rc)
  call check(rc, "config key 'field_dictionary:' in "//trim(config_file), __LINE__)
  call NUOPC_FieldDictionarySetup(trim(dictionary), rc=rc)
  call check(rc, "NUOPC_FieldDictionarySetup "//trim(dictionary), __LINE__)

  driver = ESMF_GridCompCreate(name=trim(name), config=config, rc=rc)
  call check(rc, "ESMF_GridCompCreate", __LINE__)
  call ESMF_GridCompSetServices(driver, driver_SS, userRc=user_rc, rc=rc)
  call check(rc, "SetServices", __LINE__); call check(user_rc, "SetServices (user)", __LINE__)
  call ESMF_GridCompInitialize(driver, userRc=user_rc, rc=rc)
  call check(rc, "Initialize", __LINE__); call check(user_rc, "Initialize (user)", __LINE__)
  call ESMF_GridCompRun(driver, userRc=user_rc, rc=rc)
  call check(rc, "Run", __LINE__); call check(user_rc, "Run (user)", __LINE__)
  call ESMF_GridCompGet(driver, clock=clock, rc=rc)
  call check(rc, "ESMF_GridCompGet clock", __LINE__)
  call ESMF_ClockGet(clock, advanceCount=steps, currTime=now, rc=rc)
  call check(rc, "ESMF_ClockGet", __LINE__)
  call ESMF_TimeGet(now, timeString=stamp, rc=rc)
  call check(rc, "ESMF_TimeGet", __LINE__)
  call ESMF_GridCompFinalize(driver, userRc=user_rc, rc=rc)
  call check(rc, "Finalize", __LINE__); call check(user_rc, "Finalize (user)", __LINE__)
  call ESMF_GridCompDestroy(driver, rc=rc)
  call check(rc, "ESMF_GridCompDestroy", __LINE__)
  ! The summary goes to stdout (CTest's pass expression reads it) and to the ESMF log.
  write (summary, '(a,a,i0,a,a)') trim(name)//": completed ", "", steps, " steps to ", trim(stamp)
  call ESMF_LogWrite(trim(summary), ESMF_LOGMSG_INFO, rc=rc)
  if (local_pet == 0) print '(a)', trim(summary)
  call ESMF_Finalize(rc=rc)

contains

  !> A failed call: the message names the call and the line (callers pass __LINE__), the ESMF log carries the
  !! same line through ESMF_LogFoundError, stdout carries it for CTest and the terminal, then abort.
  subroutine check(code, what, line)
    integer, intent(in) :: code
    character(len=*), intent(in) :: what
    integer, intent(in) :: line
    character(len=16) :: line_text
    if (code == ESMF_SUCCESS) return
    write (line_text, '(i0)') line
    if (ESMF_LogFoundError(rcToCheck=code, msg=trim(name)//": "//what//" failed", line=line, file=__FILE__)) &
      call abort_with(trim(name)//": "//what//" failed at "//__FILE__//":"//trim(line_text)//" (see PET*.ESMF_LogFile)")
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

end program esmApp
