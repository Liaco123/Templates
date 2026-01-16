# ==============================================================================
# Examples Build Configuration
# ==============================================================================
# Strategy: "Batch Build"
# Automatically generate a separate executable for every .cpp file in this directory.
# This avoids manually editing CMakeLists.txt when adding new simple examples.

# 1. Source Discovery
# CONFIGURE_DEPENDS: Triggers a re-configuration if files are added/removed.
file(GLOB EXAMPLE_SOURCES CONFIGURE_DEPENDS "*.cpp")

# 2. Iterate and Create Targets
foreach(file_path ${EXAMPLE_SOURCES})
    # Extract filename without extension (e.g., 'simple_demo.cpp' -> 'simple_demo')
    get_filename_component(exe_name ${file_path} NAME_WE)

    # Prefix target name to avoid collisions with the library or system tools
    # e.g., Target becomes 'ex_simple_demo'
    set(target_name "ex_${exe_name}")

    # Create Executable
    add_executable(${target_name} ${file_path})

    # Link Dependencies
    # Link against the core library via its Namespace Alias
    target_link_libraries(${target_name} PRIVATE {{name}}::{{name}})

    # Optional: Link other common example dependencies (e.g., Threading)
    # find_package(Threads REQUIRED)
    # target_link_libraries(${target_name} PRIVATE Threads::Threads)

    message(STATUS "➕ [Example] Added target: ${target_name}")
endforeach()