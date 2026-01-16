cmake_minimum_required(VERSION 3.23)

# ==============================================================================
# 1. Project Metadata & Configuration
# ==============================================================================
project({{name}} VERSION {{version}} LANGUAGES CXX)

# Enforce C++ Standard (Default: C++20)
if(NOT CMAKE_CXX_STANDARD)
    set(CMAKE_CXX_STANDARD 20)
    set(CMAKE_CXX_STANDARD_REQUIRED ON)
    set(CMAKE_CXX_EXTENSIONS OFF) # Disable compiler-specific extensions (GNU/MSVC)
endif()

# Generate 'compile_commands.json' for tooling support (VS Code / Clangd)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# ------------------------------------------------------------------------------
# Output Directory Layout
# Keeps the build directory clean by organizing artifacts into bin/ and lib/
# ------------------------------------------------------------------------------
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib")
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib")
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")

# Set Default Build Type to 'Release' for single-config generators (Ninja/Make)
get_property(isMultiConfig GLOBAL PROPERTY GENERATOR_IS_MULTI_CONFIG)
if(NOT isMultiConfig AND NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Release" CACHE STRING "Build type" FORCE)
    message(STATUS "🔧 [Config] Build Type not set. Defaulting to: Release")
endif()

# ==============================================================================
# 2. Build Options (Optimization & Debugging)
# ==============================================================================
# [Release] Link Time Optimization (IPO/LTO)
# Reduces binary size and improves execution speed.
include(CheckIPOSupported)
check_ipo_supported(RESULT result OUTPUT output)
if(result AND "${CMAKE_BUILD_TYPE}" STREQUAL "Release")
    set(CMAKE_INTERPROCEDURAL_OPTIMIZATION ON)
    message(STATUS "🚀 [Opt] LTO/IPO Enabled (Release mode)")
endif()

# [Debug] AddressSanitizer (ASAN)
# Detects memory leaks, buffer overflows, and use-after-free.
option(ENABLE_ASAN "Enable AddressSanitizer for memory safety checks" OFF)
if(ENABLE_ASAN)
    message(STATUS "🛡️ [Sanitizer] AddressSanitizer Enabled")
    if(MSVC)
        add_compile_options(/fsanitize=address)
    else()
        add_compile_options(-fsanitize=address -fno-omit-frame-pointer)
        add_link_options(-fsanitize=address)
    endif()
endif()

# ==============================================================================
# 3. Dependency Management
# ==============================================================================
# Helper macro: Find dependencies with user-friendly error messages
macro(find_dependency_safe pkg_name version)
    find_package(${pkg_name} ${version} CONFIG QUIET)
    if(NOT ${pkg_name}_FOUND)
        message(FATAL_ERROR "❌ [Dep] Missing: ${pkg_name} (${version}). Run 'conan install . --build=missing'")
    else()
        message(STATUS "✅ [Dep] Found: ${pkg_name} ${${pkg_name}_VERSION}")
    endif()
endmacro()

# [Example] Uncomment to use
# find_dependency_safe(fmt 10.1.1)

# ==============================================================================
# 4. Target Definition
# ==============================================================================
# Auto-discovery of source files using GLOB_RECURSE.
# 'CONFIGURE_DEPENDS' ensures CMake re-runs automatically when files are added/removed.
file(GLOB_RECURSE SOURCES CONFIGURE_DEPENDS "src/*.cpp")

add_executable({{name}} ${SOURCES})

# Include Paths (Private)
target_include_directories({{name}} PRIVATE 
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

# Compiler Flags & Warnings
if(MSVC)
    # /W4: High warning level
    # /utf-8: Force source files and execution character set to UTF-8 (Fixes Chinese gibberish)
    target_compile_options({{name}} PRIVATE /W4 /utf-8)
else()
    target_compile_options({{name}} PRIVATE -Wall -Wextra -Wpedantic)
endif()

# Language Features
target_compile_features({{name}} PRIVATE cxx_std_${CMAKE_CXX_STANDARD})

# Link Libraries
# target_link_libraries({{name}} PRIVATE fmt::fmt)

# ==============================================================================
# 5. Installation
# ==============================================================================
include(GNUInstallDirs)

# Install the executable to the system 'bin' directory
install(TARGETS {{name}} RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})

# ==============================================================================
# 6. Testing
# ==============================================================================
option(BUILD_TESTS "Build unit tests" OFF)

if(BUILD_TESTS)
    if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/tests")
        message(STATUS "🧪 [Sub] Adding tests...")
        enable_testing()
        add_subdirectory(tests)
    else()
        message(WARNING "⚠️ [Tests] BUILD_TESTS is ON, but 'tests' directory is missing.")
    endif()
endif()