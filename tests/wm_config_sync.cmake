# Compare a generated weather-model configuration set with its checked-in copy.
#   cmake -DGENERATED=<dir> -DCHECKED_IN=<dir> -DREFRESH=<command> -P tests/wm_config_sync.cmake
# The two directories must hold the same file names and the same bytes. On a difference the script names the
# file, prints the unified diff (checked-in against generated) and the command that refreshes the checked-in
# copy, and fails. The first line of every message starts with `wm_config_sync:` so a test can match it.
# A script run with -P has no policies set; IN_LIST needs CMP0057.
cmake_minimum_required(VERSION 3.28)

foreach(_var GENERATED CHECKED_IN REFRESH)
  if(NOT DEFINED ${_var})
    message(FATAL_ERROR "wm_config_sync: -D${_var}=... is required")
  endif()
endforeach()
if(NOT IS_DIRECTORY "${GENERATED}")
  message(FATAL_ERROR "wm_config_sync: no generated set at ${GENERATED} (build the project first)")
endif()
if(NOT IS_DIRECTORY "${CHECKED_IN}")
  message(FATAL_ERROR "wm_config_sync: no checked-in set at ${CHECKED_IN}; create it with: ${REFRESH}")
endif()

file(GLOB _generated RELATIVE "${GENERATED}" "${GENERATED}/*")
file(GLOB _checked_in RELATIVE "${CHECKED_IN}" "${CHECKED_IN}/*")
list(SORT _generated)
list(SORT _checked_in)
set(_problems 0)
foreach(_name IN LISTS _checked_in)
  if(NOT _name IN_LIST _generated)
    message("wm_config_sync: ${_name} is checked in but no longer generated")
    math(EXPR _problems "${_problems} + 1")
  endif()
endforeach()
foreach(_name IN LISTS _generated)
  if(NOT _name IN_LIST _checked_in)
    message("wm_config_sync: ${_name} is generated but not checked in")
    math(EXPR _problems "${_problems} + 1")
    continue()
  endif()
  execute_process(
    COMMAND ${CMAKE_COMMAND} -E compare_files "${CHECKED_IN}/${_name}" "${GENERATED}/${_name}"
    RESULT_VARIABLE _differs
    OUTPUT_QUIET
    ERROR_QUIET
  )
  if(_differs)
    message("wm_config_sync: ${_name} differs from the checked-in copy")
    execute_process(
      COMMAND diff -u "${CHECKED_IN}/${_name}" "${GENERATED}/${_name}"
      OUTPUT_VARIABLE _diff
      ERROR_VARIABLE _diff_error
    )
    message("${_diff}${_diff_error}")
    math(EXPR _problems "${_problems} + 1")
  endif()
endforeach()
if(_problems)
  message(
    FATAL_ERROR
    "wm_config_sync: ${_problems} file(s) out of sync between ${CHECKED_IN} and ${GENERATED}.\n"
    "If the change is intended, refresh the checked-in copy and commit it: ${REFRESH}"
  )
endif()
list(LENGTH _generated _count)
message("wm_config_sync: ${_count} file(s) in sync with ${CHECKED_IN}")
