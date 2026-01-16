# ==============================================================================
# Unit Tests Configuration (GoogleTest)
# ==============================================================================

# 1. Dependency Check
# GoogleTest should be provided by Conan (test_requires)
find_package(GTest REQUIRED)

# 2. Source Discovery
# Automatically find all .cpp files in the tests directory (e.g., src/*.cpp)
file(GLOB_RECURSE TEST_SOURCES CONFIGURE_DEPENDS "*.cpp")

# 3. Define Test Executable
# We build a single binary 'unit_tests' that contains all test cases.
add_executable(unit_tests ${TEST_SOURCES})

# 4. Link Dependencies
target_link_libraries(unit_tests PRIVATE 
    {{name}}::{{name}}    # Link the library under test
    GTest::gtest_main     # Link GTest main (provides main() entry point)
)

# 5. Compiler Settings
# Ensure tests use the same C++ standard as the library
target_compile_features(unit_tests PRIVATE cxx_std_20)

# Optional: Enable warnings for tests too
if(NOT MSVC)
    target_compile_options(unit_tests PRIVATE -Wall -Wextra)
endif()

# ==============================================================================
# CTest Integration
# ==============================================================================
# This module allows CTest (and VS Code) to enumerate individual tests
# without running them, providing a nice list in the UI.
include(GoogleTest)
gtest_discover_tests(unit_tests)