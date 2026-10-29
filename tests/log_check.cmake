# Assertions on a prototype run's ESMF PET logs, as a CTest test (cmake -P), so the evidence lives in the
# CTest result wherever the run happened (CI's ctest job, the Dockerfile test stage):
#   cmake -DRUN_DIR=<run directory> -DSPEC=<spec file> [-DFILES=<glob>] -P tests/log_check.cmake
# The spec has one directive per line (blank lines and lines starting with # are ignored); regular
# expressions are POSIX extended (grep -E), applied to every file of RUN_DIR matching FILES (default
# PET*.ESMF_LogFile; e.g. ESMF_Profile.summary for the profile run):
#   require <regex>        at least one log line matches
#   forbid <regex>         no log line matches
#   count <n> <regex>      exactly n distinct matched strings across the logs
#   grow <regex>           the regex has one capture group holding a number; on PET 0's log the last
#                          captured value must exceed the first (a quantity growing over the run)
#   below <limit> <regex>  the regex has one capture group holding an integer; it matches at least once
#                          across the logs and every captured value is at most <limit> (a ceiling)
# The first failing directive ends the test with its line in the message.

if(NOT RUN_DIR OR NOT SPEC)
  message(FATAL_ERROR "log_check: usage: cmake -DRUN_DIR=<dir> -DSPEC=<file> -P log_check.cmake")
endif()
file(STRINGS "${SPEC}" _directives)

if(NOT FILES)
  set(FILES "PET*.ESMF_LogFile")
endif()
file(GLOB _logs "${RUN_DIR}/${FILES}")
if(NOT _logs)
  message(FATAL_ERROR "log_check: no ${FILES} in ${RUN_DIR}")
endif()
file(GLOB _pet0 "${RUN_DIR}/PET0.ESMF_LogFile" "${RUN_DIR}/PET00.ESMF_LogFile" "${RUN_DIR}/PET000.ESMF_LogFile")

# grep -h -o -E <regex> <files>: the matched strings, one list element per match
function(_matches regex files out)
  execute_process(
    COMMAND grep -h -o -E -e "${regex}" ${files}
    OUTPUT_VARIABLE _text
    RESULT_VARIABLE _rc
  )
  if(_rc GREATER 1)
    message(FATAL_ERROR "log_check: grep failed (${_rc}) on '${regex}'")
  endif()
  string(REPLACE ";" "," _text "${_text}")
  string(REGEX REPLACE "\n$" "" _text "${_text}")
  if(_text STREQUAL "")
    set(${out} "" PARENT_SCOPE)
  else()
    string(REPLACE "\n" ";" _list "${_text}")
    set(${out} "${_list}" PARENT_SCOPE)
  endif()
endfunction()

set(_checked 0)
foreach(_line IN LISTS _directives)
  if(_line MATCHES "^require (.+)$")
    _matches("${CMAKE_MATCH_1}" "${_logs}" _found)
    if(NOT _found)
      message(FATAL_ERROR "log_check: required but not found: ${_line}")
    endif()
  elseif(_line MATCHES "^forbid (.+)$")
    _matches("${CMAKE_MATCH_1}" "${_logs}" _found)
    if(_found)
      list(GET _found 0 _first)
      message(FATAL_ERROR "log_check: forbidden line found (${_first}): ${_line}")
    endif()
  elseif(_line MATCHES "^count ([0-9]+) (.+)$")
    set(_want ${CMAKE_MATCH_1})
    _matches("${CMAKE_MATCH_2}" "${_logs}" _found)
    list(REMOVE_DUPLICATES _found)
    list(LENGTH _found _n)
    if(NOT _n EQUAL _want)
      message(FATAL_ERROR "log_check: ${_n} distinct matches, expected ${_want}: ${_line}")
    endif()
  elseif(_line MATCHES "^grow (.+)$")
    set(_regex "${CMAKE_MATCH_1}")
    if(NOT _pet0)
      message(FATAL_ERROR "log_check: no PET 0 log in ${RUN_DIR} for: ${_line}")
    endif()
    _matches("${_regex}" "${_pet0}" _found)
    list(LENGTH _found _n)
    if(_n LESS 2)
      message(FATAL_ERROR "log_check: ${_n} values, need at least two: ${_line}")
    endif()
    list(GET _found 0 _first_line)
    list(GET _found -1 _last_line)
    string(REGEX REPLACE "${_regex}" "\\1" _first "${_first_line}")
    string(REGEX REPLACE "${_regex}" "\\1" _last "${_last_line}")
    message(STATUS "log_check: ${_n} values, first ${_first}, last ${_last}")
    if(NOT _last GREATER _first)
      message(FATAL_ERROR "log_check: last value ${_last} does not exceed the first ${_first}: ${_line}")
    endif()
  elseif(_line MATCHES "^below ([0-9]+) (.+)$")
    set(_limit ${CMAKE_MATCH_1})
    set(_regex "${CMAKE_MATCH_2}")
    _matches("${_regex}" "${_logs}" _found)
    if(NOT _found)
      message(FATAL_ERROR "log_check: no value matched: ${_line}")
    endif()
    foreach(_match IN LISTS _found)
      string(REGEX REPLACE "${_regex}" "\\1" _value "${_match}")
      if(_value GREATER _limit)
        message(FATAL_ERROR "log_check: value ${_value} exceeds ${_limit}: ${_line}")
      endif()
    endforeach()
  elseif(_line MATCHES "^(#|$)")
    continue()
  else()
    message(FATAL_ERROR "log_check: unknown directive: ${_line}")
  endif()
  math(EXPR _checked "${_checked} + 1")
endforeach()
message(STATUS "log_check: ${_checked} check(s) passed on ${RUN_DIR}")
