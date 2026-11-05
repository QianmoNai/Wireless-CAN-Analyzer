#ifndef MOTOR_SIM_CONFIG_H
#define MOTOR_SIM_CONFIG_H
/* Select exactly ONE profile here, then rebuild and flash. */
#define SIM_DJI_C620     1
#define SIM_DJI_C610     2
#define SIM_DJI_GM6020   3
#define SIM_DM_MIT      4
#define SIM_DM_POS_VEL  5
#define SIM_DM_VEL      6
#define SIM_DM_POS_FORCE 7
#define SIM_ALL_FEEDBACK 8
#ifndef SIM_PROFILE
#define SIM_PROFILE SIM_ALL_FEEDBACK
#endif
#if SIM_PROFILE < 1 || SIM_PROFILE > 8
#error "SIM_PROFILE must select one of the eight supported profiles"
#endif
#define SIM_IS_DJI (SIM_PROFILE <= SIM_DJI_GM6020)
#define SIM_IS_DM (SIM_PROFILE >= SIM_DM_MIT && SIM_PROFILE <= SIM_DM_POS_FORCE)
#define SIM_IS_BROADCAST (SIM_PROFILE == SIM_ALL_FEEDBACK)
#if SIM_PROFILE == SIM_DJI_GM6020
#define SIM_DJI_GROUP1 0x1FFU
#define SIM_DJI_GROUP2 0x2FFU
#define SIM_DJI_FEEDBACK_BASE 0x204U
#define SIM_DJI_COMMAND_MAX 30000
#define SIM_PROFILE_NAME "DJI GM6020"
#elif SIM_PROFILE == SIM_DJI_C610
#define SIM_DJI_GROUP1 0x200U
#define SIM_DJI_GROUP2 0x1FFU
#define SIM_DJI_FEEDBACK_BASE 0x200U
#define SIM_DJI_COMMAND_MAX 10000
#define SIM_PROFILE_NAME "DJI C610 M2006"
#else
#define SIM_DJI_GROUP1 0x200U
#define SIM_DJI_GROUP2 0x1FFU
#define SIM_DJI_FEEDBACK_BASE 0x200U
#define SIM_DJI_COMMAND_MAX 16384
#if SIM_PROFILE == SIM_DJI_C620
#define SIM_PROFILE_NAME "DJI C620 M3508"
#elif SIM_PROFILE == SIM_DM_MIT
#define SIM_PROFILE_NAME "DM MIT"
#elif SIM_PROFILE == SIM_DM_POS_VEL
#define SIM_PROFILE_NAME "DM POS VEL"
#elif SIM_PROFILE == SIM_DM_VEL
#define SIM_PROFILE_NAME "DM VELOCITY"
#elif SIM_PROFILE == SIM_DM_POS_FORCE
#define SIM_PROFILE_NAME "DM POS FORCE"
#else
#define SIM_PROFILE_NAME "DJI AUTO DM QUERY"
#endif
#endif
#define SIM_DM_CONTROL_ID (SIM_DM_ID + (SIM_IS_DM ? (SIM_PROFILE-SIM_DM_MIT)*0x100U : 0U))
/* DJI feedback: 100 Hz each. Four DM nodes reply only to queries. */
#ifndef SIM_BROADCAST_PERIOD_MS
#define SIM_BROADCAST_PERIOD_MS 10U
#endif
#define SIM_BROADCAST_CYCLE_MS 8000U
/* Fixed IDs: C620 ID1=201, C610 ID2=202, GM6020 ID1=205;
 * four DM virtual nodes ID1..4, Master IDs 11..14 (hex). */
/* Build-time virtual motor configuration. No real motor power stage. */
#ifndef SIM_DJI_FIRST_ID
#define SIM_DJI_FIRST_ID 1U
#endif
#ifndef SIM_DJI_COUNT
#define SIM_DJI_COUNT 1U /* Consecutive IDs: C620/C610 1..8, GM6020 1..7. */
#endif
#ifndef SIM_DJI_FEEDBACK_MS
#define SIM_DJI_FEEDBACK_MS 1U
#endif
#ifndef SIM_COMMAND_TIMEOUT_MS
#define SIM_COMMAND_TIMEOUT_MS 500U
#endif
#ifndef SIM_DM_ID
#define SIM_DM_ID 1U /* 1..15, keeps feedback status/ID nibbles unambiguous. */
#endif
#ifndef SIM_DM_MASTER_ID
#define SIM_DM_MASTER_ID 0x000U
#endif
/* Explicit DM4310 simulation profile; controller MUST use the same limits.
 * Actual motor firmware limits can differ and are configurable on real drives. */
#ifndef SIM_DM_P_MAX
#define SIM_DM_P_MAX 12.5f
#endif
#ifndef SIM_DM_V_MAX
#define SIM_DM_V_MAX 30.0f
#endif
#ifndef SIM_DM_T_MAX
#define SIM_DM_T_MAX 10.0f
#endif
#ifndef SIM_DM_REPLY_QUEUE
#define SIM_DM_REPLY_QUEUE 32U
#endif
/* Synthetic plant parameters, NOT identified real motor characteristics. */
#ifndef SIM_DJI_MAX_RPM
#define SIM_DJI_MAX_RPM (SIM_PROFILE == SIM_DJI_GM6020 ? 320.0f : 9000.0f)
#endif
#ifndef SIM_DJI_TIME_CONSTANT
#define SIM_DJI_TIME_CONSTANT 0.15f
#endif
#ifndef SIM_DM_INERTIA
#define SIM_DM_INERTIA 0.02f
#endif
#ifndef SIM_DM_DAMPING
#define SIM_DM_DAMPING 0.10f
#endif
#endif
