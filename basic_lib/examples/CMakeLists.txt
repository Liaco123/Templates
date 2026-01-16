# === Batch Build Examples ===
# 1. 扫描目录下所有 .cpp 文件
file(GLOB EXAMPLE_FILES "${CMAKE_CURRENT_SOURCE_DIR}/*.cpp")

# 2. 循环遍历并创建 Target
foreach(file_path ${EXAMPLE_FILES})
    # 提取文件名 (e.g., 'demo_01.cpp' -> 'demo_01')
    get_filename_component(exe_name ${file_path} NAME_WE)
    
    # 可选：给 Target 加个前缀防止重名 (e.g., ex_demo_01)
    set(exe_name "ex_${exe_name}")

    add_executable(${exe_name} ${file_path})
    
    # 链接依赖库
    target_link_libraries(${exe_name} PRIVATE MyLib::MyLib)
    
    message(STATUS "➕ Added Example: ${exe_name}")
endforeach()