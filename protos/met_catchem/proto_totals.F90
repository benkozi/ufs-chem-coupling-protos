!> The hosted-import table of MET and the totals file it writes.
!!
!! A hosted import is a field another component exports and MET consumes as its own import so that the field
!! is realized and reference-shared the way CATChem will consume it later (task 5): the driver reads the rows
!! of `met_hosted_imports::` (`<standard name> <category> <variable> <source file>`) and MET records, after
!! every step, the global sum of field times cell area per level. PET 0 rewrites the record as YAML in the
!! layout of `tools.inventories.Totals` (`categories.<category>.{file, times, variables.<variable>.{units,
!! records}}`), so the conservation checker reads this file and the task-2 `totals.yaml` with one model.
!! Variable names are quoted (YAML 1.1 reads an unquoted NO as false) and time stamps carry a trailing Z.
module proto_totals
  use ESMF
  implicit none
  private

  public :: hosted_spec, proto_totals_write

  integer, parameter, public :: HOSTED_NAME_LEN = 128
  integer, parameter, public :: STAMP_LEN = 32

  type hosted_spec
    character(len=HOSTED_NAME_LEN) :: standard_name = ''
    character(len=HOSTED_NAME_LEN) :: category = ''
    character(len=HOSTED_NAME_LEN) :: variable = ''
    character(len=ESMF_MAXPATHLEN) :: file = ''
    character(len=HOSTED_NAME_LEN) :: units = ''
    integer :: nlev = 0
  end type hosted_spec

contains

  !> Rewrite `path` with every record so far: totals(level, record, hosted), stamps(record).
  subroutine proto_totals_write(path, hosted, nrec, stamps, totals, rc)
    character(len=*), intent(in) :: path
    type(hosted_spec), intent(in) :: hosted(:)
    integer, intent(in) :: nrec
    character(len=STAMP_LEN), intent(in) :: stamps(:)
    real(ESMF_KIND_R8), intent(in) :: totals(:, :, :)
    integer, intent(out) :: rc
    integer :: unit, ios, h, g, r, k
    logical :: first_of_category
    character(len=24) :: number

    rc = ESMF_SUCCESS
    open (newunit=unit, file=path, status='replace', action='write', iostat=ios)
    if (ios /= 0) then
      call ESMF_LogSetError(ESMF_RC_FILE_OPEN, msg="totals: cannot open "//trim(path), line=__LINE__, file=__FILE__, rcToReturn=rc)
      return
    end if
    write (unit, '(a)') 'categories:'
    do g = 1, size(hosted)
      ! one block per distinct category, in first-appearance order
      first_of_category = .true.
      do h = 1, g - 1
        if (hosted(h)%category == hosted(g)%category) first_of_category = .false.
      end do
      if (.not. first_of_category) cycle
      write (unit, '(a)') "  '"//trim(hosted(g)%category)//"':"
      write (unit, '(a)') "    file: '"//trim(hosted(g)%file)//"'"
      write (unit, '(a)') '    times:'
      do r = 1, nrec
        write (unit, '(a)') "    - '"//trim(stamps(r))//"'"
      end do
      write (unit, '(a)') '    variables:'
      do h = 1, size(hosted)
        if (hosted(h)%category /= hosted(g)%category) cycle
        write (unit, '(a)') "      '"//trim(hosted(h)%variable)//"':"
        write (unit, '(a)') "        units: '"//trim(hosted(h)%units)//"'"
        write (unit, '(a)') '        records:'
        do r = 1, nrec
          do k = 1, hosted(h)%nlev
            write (number, '(es24.16)') totals(k, r, h)
            if (k == 1) then
              write (unit, '(a)') '        - - '//trim(adjustl(number))
            else
              write (unit, '(a)') '          - '//trim(adjustl(number))
            end if
          end do
        end do
      end do
    end do
    close (unit)
  end subroutine proto_totals_write

end module proto_totals
