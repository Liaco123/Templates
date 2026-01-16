cmake_minimum_required(VERSION 3.23)

# ==============================================================================
# 1. Project Configuration
# ==============================================================================
project({{name}} VERSION {{version}} LANGUAGES CXX)

# Note: For header-only libraries, we don't set global C++ standards.
# Instead, we define interface requirements that propagate to the consumer.

# ==============================================================================
# 2. Interface Target Definition
# ==============================================================================
# INTERFACE libraries do not compile to binary. They are just a collection of 
# usage requirements (include paths, flags, defines) used by consumers.
add_library({{name}} INTERFACE)
add_library({{name}}::{{name}} ALIAS {{name}})

# ==============================================================================
# 3. Usage Requirements (Propagated to Consumers)
# ==============================================================================
# 1. C++ Standard Requirement
# Any target linking this library will be forced to use at least C++20.
target_compile_features({{name}} INTERFACE cxx_std_20)

# 2. Include Directories
target_include_directories({{name}} INTERFACE
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
)

# 3. Dependencies (Transitive)
# If your header includes <fmt/core.h>, you must link fmt here.
# 'INTERFACE' ensures the consumer also links fmt automatically.
# find_package(fmt REQUIRED CONFIG)
# target_link_libraries({{name}} INTERFACE fmt::fmt)

# ==============================================================================
# 4. Installation & Export
# ==============================================================================
include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

# Install the Interface Target
# Note: No 'ARCHIVE' or 'LIBRARY' or 'RUNTIME' destinations needed.
install(TARGETS {{name}}
    EXPORT {{name}}Targets
    INCLUDES DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
)

# Install Headers (The actual content)
install(DIRECTORY include/ DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})

# Generate 'ConfigVersion.cmake' (Header-only libs are often compatible with any newer version)
write_basic_package_version_file(
    "${CMAKE_CURRENT_BINARY_DIR}/{{name}}ConfigVersion.cmake"
    VERSION ${PROJECT_VERSION}
    COMPATIBILITY AnyNewerVersion 
)

# Generate 'Config.cmake'
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

# Install Export Set
install(EXPORT {{name}}Targets
    FILE {{name}}Targets.cmake
    NAMESPACE {{name}}::
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/{{name}}
)

# ==============================================================================
# 5. Tests & Examples (Crucial for Header-Only Libs)
# ==============================================================================
# Since there is no .cpp to compile in the lib, tests are the ONLY way 
# to verify the code compiles correctly.
option(BUILD_TESTS "Build tests" OFF)
option(BUILD_EXAMPLES "Build examples" OFF)

if(BUILD_TESTS)
    message(STATUS "🧪 [Sub] Adding tests...")
    enable_testing()
    add_subdirectory(tests)
endif()

if(BUILD_EXAMPLES)
    message(STATUS "📂 [Sub] Adding examples...")
    add_subdirectory(examples)
endif()