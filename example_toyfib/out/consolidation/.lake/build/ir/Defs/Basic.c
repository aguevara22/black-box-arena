// Lean compiler output
// Module: Defs.Basic
// Imports: public import Init public meta import Init
#include <lean/lean.h>
#if defined(__clang__)
#pragma clang diagnostic ignored "-Wunused-parameter"
#pragma clang diagnostic ignored "-Wunused-label"
#elif defined(__GNUC__) && !defined(__CLANG__)
#pragma GCC diagnostic ignored "-Wunused-parameter"
#pragma GCC diagnostic ignored "-Wunused-label"
#pragma GCC diagnostic ignored "-Wunused-but-set-variable"
#endif
#ifdef __cplusplus
extern "C" {
#endif
uint8_t lean_nat_dec_eq(lean_object*, lean_object*);
lean_object* lean_nat_sub(lean_object*, lean_object*);
lean_object* lean_nat_add(lean_object*, lean_object*);
LEAN_EXPORT lean_object* lp_consolidation_Arena_double(lean_object*);
LEAN_EXPORT lean_object* lp_consolidation_Arena_double___boxed(lean_object*);
LEAN_EXPORT lean_object* lp_consolidation_Arena_double(lean_object* v_x_1_){
_start:
{
lean_object* v_zero_2_; uint8_t v_isZero_3_; 
v_zero_2_ = lean_unsigned_to_nat(0u);
v_isZero_3_ = lean_nat_dec_eq(v_x_1_, v_zero_2_);
if (v_isZero_3_ == 1)
{
return v_zero_2_;
}
else
{
lean_object* v_one_4_; lean_object* v_n_5_; lean_object* v___x_6_; lean_object* v___x_7_; lean_object* v___x_8_; 
v_one_4_ = lean_unsigned_to_nat(1u);
v_n_5_ = lean_nat_sub(v_x_1_, v_one_4_);
v___x_6_ = lp_consolidation_Arena_double(v_n_5_);
lean_dec(v_n_5_);
v___x_7_ = lean_unsigned_to_nat(2u);
v___x_8_ = lean_nat_add(v___x_6_, v___x_7_);
lean_dec(v___x_6_);
return v___x_8_;
}
}
}
LEAN_EXPORT lean_object* lp_consolidation_Arena_double___boxed(lean_object* v_x_9_){
_start:
{
lean_object* v_res_10_; 
v_res_10_ = lp_consolidation_Arena_double(v_x_9_);
lean_dec(v_x_9_);
return v_res_10_;
}
}
lean_object* initialize_Init(uint8_t builtin);
lean_object* initialize_Init(uint8_t builtin);
static bool _G_initialized = false;
LEAN_EXPORT lean_object* initialize_consolidation_Defs_Basic(uint8_t builtin) {
lean_object * res;
if (_G_initialized) return lean_io_result_mk_ok(lean_box(0));
_G_initialized = true;
res = initialize_Init(builtin);
if (lean_io_result_is_error(res)) return res;
lean_dec_ref(res);
res = initialize_Init(builtin);
if (lean_io_result_is_error(res)) return res;
lean_dec_ref(res);
return lean_io_result_mk_ok(lean_box(0));
}
#ifdef __cplusplus
}
#endif
