#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
common=(-std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined -g -I "$project_dir/Core/Inc")
cc "${common[@]}" "$project_dir/tests/test_can_load_bench.c" "$project_dir/Core/Src/can_load_bench.c" -o "$test_dir/test_load"
"$test_dir/test_load"
for profile in 1 2 3 4 5 6 7; do
 cc "${common[@]}" -DSIM_PROFILE="$profile" "$project_dir/tests/test_motor_sim.c" "$project_dir/Core/Src/motor_sim.c" -o "$test_dir/test"
 "$test_dir/test"
done
for profile in 1 2 3; do
 count=4
 if [ "$profile" = 3 ]; then count=3; fi
 cc "${common[@]}" -DSIM_PROFILE="$profile" -DSIM_DJI_FIRST_ID=5 -DSIM_DJI_COUNT="$count" "$project_dir/tests/test_dji_group.c" "$project_dir/Core/Src/motor_sim.c" -o "$test_dir/test_group"
 "$test_dir/test_group"
done

cc "${common[@]}" -DSIM_PROFILE=SIM_ALL_FEEDBACK "$project_dir/tests/test_broadcast.c" "$project_dir/Core/Src/motor_sim.c" -o "$test_dir/test_broadcast"
"$test_dir/test_broadcast"

for profile in 1 2 3 4 5 6 7 8; do
 cc "${common[@]}" -DSIM_PROFILE="$profile" "$project_dir/tests/test_status_query.c" "$project_dir/Core/Src/motor_sim.c" -o "$test_dir/test_query"
 "$test_dir/test_query"
done
# Verify status reads use the configured base node ID and Master ID in every mode.
for profile in 4 5 6 7; do
 cc "${common[@]}" -DSIM_PROFILE="$profile" -DSIM_DM_ID=9 -DSIM_DM_MASTER_ID=0x55 "$project_dir/tests/test_status_query.c" "$project_dir/Core/Src/motor_sim.c" -o "$test_dir/test_query_custom"
 "$test_dir/test_query_custom"
done

for enabled in 0 1; do
 cc "${common[@]}" -DSIM_IMU_ENABLED="$enabled" "$project_dir/tests/test_imu_sim.c" "$project_dir/Core/Src/imu_sim.c" -lm -o "$test_dir/test_imu"
 "$test_dir/test_imu"
done
cc "${common[@]}" -DSIM_IMU_ENABLED=1 -DSIM_IMU_CAN_ID=0x150 -DSIM_IMU_MASTER_ID=0x160 "$project_dir/tests/test_imu_sim.c" "$project_dir/Core/Src/imu_sim.c" -lm -o "$test_dir/test_imu_custom"
"$test_dir/test_imu_custom"
