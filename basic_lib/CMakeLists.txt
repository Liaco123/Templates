cmake_minimum_required(VERSION 3.23)

# ==============================================================================
# 1. Project Configuration
# ==============================================================================
project({{name}} VERSION {{version}} LANGUAGES CXX)

# Set C++ Standard (Default: C++20)
if(NOT CMAKE_CXX_STANDARD)
    set(CMAKE_CXX_STANDARD 20)
    set(CMAKE_CXX_STANDARD_REQUIRED ON)
    set(CMAKE_CXX_EXTENSIONS OFF)
endif()

# Generate compile_commands.json for tooling (LSP, Clangd, VSCode)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Default Build Type to 'Release' if not specified (for Single-Config generators)
get_property(isMultiConfig GLOBAL PROPERTY GENERATOR_IS_MULTI_CONFIG)
if(NOT isMultiConfig AND NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Release" CACHE STRING "Build type" FORCE)
    message(STATUS "🔧 [Config] Build Type not set. Defaulting to: Release")
endif()

# Helper Macro: Safe dependency finding with clear error messages
macro(find_dependency_safe pkg_name version)
    find_package(${pkg_name} ${version} CONFIG QUIET)
    if(NOT ${pkg_name}_FOUND)
        message(FATAL_ERROR "❌ [Dep] Missing: ${pkg_name} (${version}). Run 'conan install . --build=missing'")
    else()
        message(STATUS "✅ [Dep] Found: ${pkg_name} ${${pkg_name}_VERSION}")
    endif()
endmacro()

# ==============================================================================
# 2. Dependencies
# ==============================================================================
# Add your core dependencies here, e.g.:
# find_dependency_safe(fmt 10.1.0)

# ==============================================================================
# 3. Target Definition
# ==============================================================================
# NOTE: 'BUILD_SHARED_LIBS' controls static/shared build.
add_library({{name}} src/{{name}}.cpp)
add_library({{name}}::{{name}} ALIAS {{name}})

# Compiler Warnings (Best Practices)
if(MSVC)
    target_compile_options({{name}} PRIVATE /W4)
else()
    target_compile_options({{name}} PRIVATE -Wall -Wextra -Wpedantic)
endif()

# Compile Features & Includes
target_compile_features({{name}} PUBLIC cxx_std_${CMAKE_CXX_STANDARD})
target_include_directories({{name}} PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
)

# Link Libraries
# target_link_libraries({{name}} PRIVATE fmt::fmt)

# ==============================================================================
# 4. Installation & Export (Standard CMake Packaging)
# ==============================================================================
include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

# Install Targets (Binaries/Libraries)
install(TARGETS {{name}}
    EXPORT {{name}}Targets
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
    INCLUDES DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
)

# Install Headers
install(DIRECTORY include/ DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})

# Generate 'ConfigVersion.cmake' (Versioning support)
write_basic_package_version_file(
    "${CMAKE_CURRENT_BINARY_DIR}/{{name}}ConfigVersion.cmake"
    VERSION ${PROJECT_VERSION}
    COMPATIBILITY SameMajorVersion
)

# Generate 'Config.cmake' (Project discovery support)
configure_package_config_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/cmake/{{name}}Config.cmake.in"
    "${CMAKE_CURRENT_BINARY_DIR}/{{name}}Config.cmake"
    INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/{{name}}
)

# Install Config Files
install(FILES
    "${CMAKE_CURRENT_BINARY_DIR}/{{name}}Config.cmake"
    "${CMAKE_CURRENT_BINARY_DIR}/{{name}}ConfigVersion.cmake"
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/{{name}}
)

# Install Export Set (Target definitions)
install(EXPORT {{name}}Targets
    FILE {{name}}Targets.cmake
    NAMESPACE {{name}}::
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/{{name}}
)

# ==============================================================================
# 5. Subdirectories (Examples & Tests)
# ==============================================================================
option(BUILD_EXAMPLES "Build examples" OFF)
option(BUILD_TESTS "Build tests" OFF)

if(BUILD_EXAMPLES)
    message(STATUS "📂 [Sub] Adding examples...")
    add_subdirectory(examples)
endif()

if(BUILD_TESTS)
    message(STATUS "🧪 [Sub] Adding tests...")
    enable_testing()
    add_subdirectory(tests)
endif()