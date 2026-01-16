#include <gtest/gtest.h>

// 引入你的库头文件
// 注意：因为我们在 CMake 里链接了库，所以这里可以直接 include
#include "{{name}}/{{name}}.h"

// === 示例 1: 基础数值测试 ===
TEST(MathTest, AdditionWorks) {
  // 假设你的库里有个 add 函数
  // int result = {{name}}::add(1, 2);
  int result = 3;  // 模拟结果

  EXPECT_EQ(result, 3);
  EXPECT_NE(result, 4);
}

// === 示例 2: 字符串/逻辑测试 ===
TEST(LogicTest, StringCheck) {
  std::string robot_status = "Idle";

  EXPECT_STREQ(robot_status.c_str(), "Idle");
  EXPECT_FALSE(robot_status.empty());
}

// === 示例 3: 浮点数测试 (机器人常用) ===
TEST(RobotArmTest, JointAngleConfig) {
  double target_angle = 3.14159;
  double current_angle = 3.14150;

  // 浮点数不能用 EQ，要用 NEAR (允许误差范围 1e-4)
  EXPECT_NEAR(current_angle, target_angle, 1e-4);
}