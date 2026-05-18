cmake_minimum_required(VERSION 3.23)

project({{name}} VERSION {{version}} LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20 CACHE STRING "C++ standard")
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib")
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib")
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")

get_property(isMultiConfig GLOBAL PROPERTY GENERATOR_IS_MULTI_CONFIG)
if(NOT isMultiConfig AND NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Release" CACHE STRING "Build type" FORCE)
    message(STATUS "[config] CMAKE_BUILD_TYPE not set; defaulting to Release")
endif()

option({{name}}_ENABLE_IPO "Enable IPO/LTO in Release builds when supported" ON)
option({{name}}_ENABLE_ASAN "Enable AddressSanitizer" OFF)
option({{name}}_BUILD_TESTS "Build unit tests" OFF)

include(CheckIPOSupported)
if({{name}}_ENABLE_IPO)
    check_ipo_supported(RESULT ipo_supported OUTPUT ipo_error)
    if(ipo_supported)
        set(CMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE ON)
        message(STATUS "[opt] IPO/LTO enabled for Release builds")
    else()
        message(STATUS "[opt] IPO/LTO unavailable: ${ipo_error}")
    endif()
endif()

if({{name}}_ENABLE_ASAN)
    message(STATUS "[sanitizer] AddressSanitizer enabled")
    if(MSVC)
        add_compile_options(/fsanitize=address)
    else()
        add_compile_options(-fsanitize=address -fno-omit-frame-pointer)
        add_link_options(-fsanitize=address)
    endif()
endif()

macro(find_dependency_safe pkg_name version)
    find_package(${pkg_name} ${version} CONFIG QUIET)
    if(NOT ${pkg_name}_FOUND)
        message(FATAL_ERROR "[dep] Missing ${pkg_name} ${version}. Run: conan install . --build=missing")
    endif()
    message(STATUS "[dep] Found ${pkg_name} ${${pkg_name}_VERSION}")
endmacro()

# Example:
# find_dependency_safe(fmt 10.1.1)

file(GLOB_RECURSE SOURCES CONFIGURE_DEPENDS "src/*.cpp")

add_executable({{name}})
target_sources({{name}} PRIVATE ${SOURCES})

target_include_directories({{name}} PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
)

if(MSVC)
    target_compile_options({{name}} PRIVATE /W4 /utf-8)
else()
    target_compile_options({{name}} PRIVATE -Wall -Wextra -Wpedantic)
endif()

target_compile_features({{name}} PRIVATE cxx_std_20)

# Example:
# target_link_libraries({{name}} PRIVATE fmt::fmt)

include(GNUInstallDirs)

install(TARGETS {{name}} RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})

if({{name}}_BUILD_TESTS)
    if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/tests")
        enable_testing()
        add_subdirectory(tests)
    else()
        message(WARNING "[tests] {{name}}_BUILD_TESTS is ON, but the tests directory is missing")
    endif()
endif()
