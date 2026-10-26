! Initializes ESMF and prints its version string; the test expects ESMF_VERSION_STRING=8.9.1...
program esmf_version
  use ESMF
  implicit none
  integer :: rc

  call ESMF_Initialize(logkindflag=ESMF_LOGKIND_NONE, rc=rc)
  if (rc /= ESMF_SUCCESS) error stop "ESMF_Initialize failed"
  print '(a)', "ESMF_VERSION_STRING="//ESMF_VERSION_STRING
  call ESMF_Finalize(rc=rc)
  if (rc /= ESMF_SUCCESS) error stop "ESMF_Finalize failed"
end program esmf_version
