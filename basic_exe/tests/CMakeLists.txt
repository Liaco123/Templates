find_package(GTest REQUIRED)

file(GLOB_RECURSE TEST_SOURCES CONFIGURE_DEPENDS "*.cpp")

add_executable(unit_tests ${TEST_SOURCES})

target_include_directories(unit_tests PRIVATE
    "${PROJECT_SOURCE_DIR}/include"
)

target_link_libraries(unit_tests PRIVATE GTest::gtest_main)

target_compile_features(unit_tests PRIVATE cxx_std_20)

if(MSVC)
    target_compile_options(unit_tests PRIVATE /W4 /utf-8)
else()
    target_compile_options(unit_tests PRIVATE -Wall -Wextra)
endif()

include(GoogleTest)
gtest_discover_tests(unit_tests)
