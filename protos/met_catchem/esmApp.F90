!> esmApp: the prototype driver's program (met_catchem, cece_met: the config's `prototype:` names it).
!!
!!   esmApp [esm.configure]              run (the config defaults to ./esm.configure)
!!   esmApp --dump-manifest <manifest>   print every manifest value the components read, then exit
!!
!! The field dictionary named in the config is loaded before the driver exists; on success PET 0 prints
!! "<prototype>: completed <n> steps to <stop time>", on failure the error, then a normal finalize and a
!! non-zero exit on every PET (never an abort: MPI_Abort races the stdout forwarding and can lose the message).
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
  character(len=256) :: failure = ""
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
      if (command_argument_count() < 2) call stop_with("usage: esmApp --dump-manifest <manifest.yaml>")
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
  call report_memory("after init")
  call ESMF_GridCompRun(driver, userRc=user_rc, rc=rc)
  call check(rc, "Run", __LINE__); call check(user_rc, "Run (user)", __LINE__)
  call report_memory("at end")
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

  !> Per-PET memory (ESMF_VMGetMemInfo, KB, current not peak; NUOPC_Compliance_Base.F90 2307-2397 is the
  !! 8.9.1 caller): every PET logs its own numbers, PET 0 prints the maxima over PETs (ESMF_VMAllReduce).
  subroutine report_memory(when)
    character(len=*), intent(in) :: when
    integer :: mine(2), most(2)
    character(len=256) :: line
    call ESMF_VMGetMemInfo(mine(1), mine(2), rc=rc)
    call check(rc, "ESMF_VMGetMemInfo", __LINE__)
    write (line, '(a,i0,a,i0,a)') trim(name)//": memory of this PET virt ", mine(1), " KB phys ", mine(2), " KB ("//when//")"
    call ESMF_LogWrite(trim(line), ESMF_LOGMSG_INFO, rc=rc)
    call ESMF_VMAllReduce(vm, sendData=mine, recvData=most, count=2, reduceflag=ESMF_REDUCE_MAX, rc=rc)
    call check(rc, "ESMF_VMAllReduce memory", __LINE__)
    write (line, '(a,i0,a,i0,a)') trim(name)//": memory per PET max virt ", most(1), " KB phys ", most(2), " KB ("//when//")"
    call ESMF_LogWrite(trim(line), ESMF_LOGMSG_INFO, rc=rc)
    if (local_pet == 0) print '(a)', trim(line)
  end subroutine report_memory

  !> A failed call, decided collectively: every PET reaches this reduce after every checked call, so a failure
  !! on any PET ends the run on all of them through a normal ESMF_Finalize (MPI_Finalize, every rank's stdout
  !! drained) and a non-zero exit. The failing PETs print the message (with the call and the line, callers pass
  !! __LINE__) and log it through ESMF_LogFoundError. ESMF_END_ABORT is not used: develop run 40597634701 lost
  !! PET 0's PET-rule message when another rank's MPI_Abort interrupted Open MPI's output forwarding.
  subroutine check(code, what, line)
    integer, intent(in) :: code
    character(len=*), intent(in) :: what
    integer, intent(in) :: line
    character(len=16) :: line_text
    integer :: failed(1), any_failed, ignore
    logical :: logged
    failed(1) = 0
    if (code /= ESMF_SUCCESS) then
      failed(1) = 1
      write (line_text, '(i0)') line
      failure = trim(name)//": "//what//" failed at "//__FILE__//":"//trim(line_text)//" (see PET*.ESMF_LogFile)"
      logged = ESMF_LogFoundError(rcToCheck=code, msg=trim(failure), line=line, file=__FILE__)
    end if
    call ESMF_VMAllFullReduce(vm, sendData=failed, recvData=any_failed, count=1, reduceflag=ESMF_REDUCE_MAX, rc=ignore)
    if (any_failed == 0) return
    if (failed(1) == 1) then
      print '(a)', trim(failure)
      flush (6)
    end if
    call finish_with_error()
  end subroutine check

  !> A uniform configuration error (every PET sees it): PET 0 prints it, then the same normal termination.
  subroutine stop_with(msg)
    character(len=*), intent(in) :: msg
    integer :: ignore
    if (local_pet == 0) then
      print '(a)', msg
      flush (6)
    end if
    call ESMF_LogWrite(msg, ESMF_LOGMSG_ERROR, rc=ignore)
    call finish_with_error()
  end subroutine stop_with

  subroutine finish_with_error()
    integer :: ignore
    call ESMF_Finalize(endflag=ESMF_END_NORMAL, rc=ignore)
    error stop 1
  end subroutine finish_with_error

end program esmApp
