! Initializes ESMF and prints its version string; the test expects ESMF_VERSION_STRING=8.9.1...
! Return codes are checked with the ESMF_LogFoundError idiom used throughout the CATChem and CECE
! caps and apps; at program level a failure aborts through ESMF_Finalize(endflag=ESMF_END_ABORT).
program esmf_version
  use ESMF
  implicit none
  integer :: rc

  call ESMF_Initialize(logkindflag=ESMF_LOGKIND_NONE, rc=rc)
  if (ESMF_LogFoundError(rcToCheck=rc, msg=ESMF_LOGERR_PASSTHRU, &
    line=__LINE__, file=__FILE__)) call ESMF_Finalize(endflag=ESMF_END_ABORT)

  print '(a)', "ESMF_VERSION_STRING="//ESMF_VERSION_STRING

  call ESMF_Finalize()
end program esmf_version
