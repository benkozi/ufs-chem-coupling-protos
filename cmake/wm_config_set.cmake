# A weather-model configuration set: the files a ufs-weather-model regression test stages for a CATChem run fed
# by CECE (issue #16), kept in the repository as a reviewable hard copy.
#
#   protos_wm_config_set(
#     NAME <set> MANIFEST_DEFAULTS <yaml> GEN_DEFAULTS <yaml> BASE_CONFIG <CATChem science config> RESOLUTION <res>
#   )
#
# registers, with paths relative to the repository root:
#   wm_configs_<set>            target, part of `all`: tools/gen_configs.py in the run-directory layout writes the
#                               set to <build>/wm_configs/<set>/ whenever a defaults file, the base config or a
#                               tools/*.py file changed. The build never writes into the source tree.
#   wm_configs_<set>_in_sync    test (ci): the build-tree set equals configs/wm/<set>/ byte for byte; on a
#                               difference it names the file and prints the diff and the refresh command.
#   wm_configs_<set>_stale_copy_fails  test (ci): the rule's failure case, on a copy with one changed file.
#   update_wm_configs_<set>     target, not part of `all`: writes the set into configs/wm/<set>/ for a person
#                               to review and commit.
function(protos_wm_config_set)
  cmake_parse_arguments(PARSE_ARGV 0 _wm "" "NAME;MANIFEST_DEFAULTS;GEN_DEFAULTS;BASE_CONFIG;RESOLUTION" "")
  foreach(_arg NAME MANIFEST_DEFAULTS GEN_DEFAULTS BASE_CONFIG RESOLUTION)
    if(NOT _wm_${_arg})
      message(FATAL_ERROR "protos_wm_config_set: ${_arg} is required")
    endif()
  endforeach()
  find_program(UV_EXECUTABLE uv REQUIRED)

  set(_generated ${PROJECT_BINARY_DIR}/wm_configs/${_wm_NAME})
  set(_checked_in ${PROJECT_SOURCE_DIR}/configs/wm/${_wm_NAME})
  set(_stamp ${PROJECT_BINARY_DIR}/wm_configs/${_wm_NAME}.stamp)
  set(_sync ${PROJECT_SOURCE_DIR}/tests/wm_config_sync.cmake)
  set(_refresh "cmake --build ${PROJECT_BINARY_DIR} --target update_wm_configs_${_wm_NAME}")
  # `uv run --no-sync` uses the image's environment; the working directory is the repository root, so the
  # settings' relative paths resolve and the provenance comments name repository-relative files.
  set(
    _generate
    ${UV_EXECUTABLE} run --no-sync python -m tools.gen_configs --layout run-dir
    --from-defaults ${_wm_MANIFEST_DEFAULTS} --defaults ${_wm_GEN_DEFAULTS}
    --catchem-base-config ${_wm_BASE_CONFIG} --resolution ${_wm_RESOLUTION} --log-level WARNING
  )
  file(GLOB _tools CONFIGURE_DEPENDS ${PROJECT_SOURCE_DIR}/tools/*.py)

  add_custom_command(
    OUTPUT ${_stamp}
    COMMAND ${CMAKE_COMMAND} -E rm -rf ${_generated}
    COMMAND ${_generate} --out ${_generated}
    COMMAND ${CMAKE_COMMAND} -E touch ${_stamp}
    DEPENDS
      ${PROJECT_SOURCE_DIR}/${_wm_MANIFEST_DEFAULTS} ${PROJECT_SOURCE_DIR}/${_wm_GEN_DEFAULTS}
      ${PROJECT_SOURCE_DIR}/${_wm_BASE_CONFIG} ${_tools}
    WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}
    COMMENT "weather-model configuration set ${_wm_NAME} -> ${_generated}"
    VERBATIM
  )
  add_custom_target(wm_configs_${_wm_NAME} ALL DEPENDS ${_stamp})

  add_custom_target(
    update_wm_configs_${_wm_NAME}
    COMMAND ${_generate} --out ${_checked_in}
    WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}
    COMMENT "refreshing the checked-in set ${_checked_in}; review the diff and commit it"
    VERBATIM
  )

  add_test(
    NAME wm_configs_${_wm_NAME}_in_sync
    COMMAND
      ${CMAKE_COMMAND} -DGENERATED=${_generated} -DCHECKED_IN=${_checked_in} "-DREFRESH=${_refresh}" -P ${_sync}
  )
  set_tests_properties(wm_configs_${_wm_NAME}_in_sync PROPERTIES LABELS ci TIMEOUT 60)

  # The failure case: the checked-in set with one file changed must fail, naming that file.
  if(IS_DIRECTORY ${_checked_in})
    set(_stale ${PROJECT_BINARY_DIR}/wm_configs_stale/${_wm_NAME})
    file(REMOVE_RECURSE ${_stale})
    file(COPY ${_checked_in}/ DESTINATION ${_stale})
    file(APPEND ${_stale}/cece.yaml "# a line the generator does not write\n")
    add_test(
      NAME wm_configs_${_wm_NAME}_stale_copy_fails
      COMMAND
        ${CMAKE_COMMAND} -DGENERATED=${_generated} -DCHECKED_IN=${_stale} "-DREFRESH=${_refresh}" -P ${_sync}
    )
    set_tests_properties(
      wm_configs_${_wm_NAME}_stale_copy_fails
      PROPERTIES
        LABELS ci
        PASS_REGULAR_EXPRESSION "wm_config_sync: cece.yaml differs from the checked-in copy"
        TIMEOUT 60
    )
  else()
    message(WARNING "no checked-in set at ${_checked_in}; create it with: ${_refresh}")
  endif()
endfunction()
