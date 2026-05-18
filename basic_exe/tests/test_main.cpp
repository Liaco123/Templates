#include <gtest/gtest.h>

#include "{{name}}.hpp"

TEST(AppTest, AnswerIsStable) {
  EXPECT_EQ(app_answer(), 42);
}

TEST(LogicTest, StringCheck) {
  std::string robot_status = "Idle";

  EXPECT_STREQ(robot_status.c_str(), "Idle");
  EXPECT_FALSE(robot_status.empty());
}

TEST(RobotArmTest, JointAngleConfig) {
  double target_angle = 3.14159;
  double current_angle = 3.14150;

  EXPECT_NEAR(current_angle, target_angle, 1e-4);
}
