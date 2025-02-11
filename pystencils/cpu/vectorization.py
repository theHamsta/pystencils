import warnings
from typing import Container, Union

import sympy as sp

import pystencils.astnodes as ast
from pystencils.backends.simd_instruction_sets import get_vector_instruction_set
from pystencils.data_types import (
    PointerType, TypedSymbol, VectorType, cast_func, collate_types, get_type_of_expression,
    vector_memory_access)
from pystencils.fast_approximation import fast_division, fast_inv_sqrt, fast_sqrt
from pystencils.field import Field
from pystencils.integer_functions import modulo_ceil, modulo_floor
from pystencils.sympyextensions import fast_subs
from pystencils.transformations import (
    cut_loop, filtered_tree_iteration, replace_inner_stride_with_one)


# noinspection PyPep8Naming
class vec_any(sp.Function):
    nargs = (1, )


# noinspection PyPep8Naming
class vec_all(sp.Function):
    nargs = (1, )


def vectorize(kernel_ast: ast.KernelFunction, instruction_set: str = 'avx',
              assume_aligned: bool = False, nontemporal: Union[bool, Container[Union[str, Field]]] = False,
              assume_inner_stride_one: bool = False, assume_sufficient_line_padding: bool = True):
    """Explicit vectorization using SIMD vectorization via intrinsics.

    Args:
        kernel_ast: abstract syntax tree (KernelFunction node)
        instruction_set: one of the supported vector instruction sets, currently ('sse', 'avx' and 'avx512')
        assume_aligned: assume that the first inner cell of each line is aligned. If false, only unaligned-loads are
                        used. If true, some of the loads are assumed to be from aligned memory addresses.
                        For example if x is the fastest coordinate, the access to center can be fetched via an
                        aligned-load instruction, for the west or east accesses potentially slower unaligend-load
                        instructions have to be used.
        nontemporal: a container of fields or field names for which nontemporal (streaming) stores are used.
                     If true, nontemporal access instructions are used for all fields.
        assume_inner_stride_one: kernels with non-constant inner loop bound and strides can not be vectorized since
                                 the inner loop stride is a runtime variable and thus might not be always 1.
                                 If this parameter is set to true, the inner stride is assumed to be always one.
                                 This has to be ensured at runtime!
        assume_sufficient_line_padding: if True and assume_inner_stride_one, no tail loop is created but loop is
                                        extended by at most (vector_width-1) elements
                                        assumes that at the end of each line there is enough padding with dummy data
                                        depending on the access pattern there might be additional padding
                                        required at the end of the array
    """
    if instruction_set is None:
        return
    
    all_fields = kernel_ast.fields_accessed
    if nontemporal is None or nontemporal is False:
        nontemporal = {}
    elif nontemporal is True:
        nontemporal = all_fields

    if assume_inner_stride_one:
        replace_inner_stride_with_one(kernel_ast)

    field_float_dtypes = set(f.dtype for f in all_fields if f.dtype.is_float())
    if len(field_float_dtypes) != 1:
        raise NotImplementedError("Cannot vectorize kernels that contain accesses "
                                  "to differently typed floating point fields")
    float_size = field_float_dtypes.pop().numpy_dtype.itemsize
    assert float_size in (8, 4)
    vector_is = get_vector_instruction_set('double' if float_size == 8 else 'float',
                                           instruction_set=instruction_set)
    vector_width = vector_is['width']
    kernel_ast.instruction_set = vector_is

    vectorize_inner_loops_and_adapt_load_stores(kernel_ast, vector_width, assume_aligned,
                                                nontemporal, assume_sufficient_line_padding)
    insert_vector_casts(kernel_ast)


def vectorize_inner_loops_and_adapt_load_stores(ast_node, vector_width, assume_aligned, nontemporal_fields,
                                                assume_sufficient_line_padding):
    """Goes over all innermost loops, changes increment to vector width and replaces field accesses by vector type."""
    all_loops = filtered_tree_iteration(ast_node, ast.LoopOverCoordinate, stop_type=ast.SympyAssignment)
    inner_loops = [n for n in all_loops if n.is_innermost_loop]
    zero_loop_counters = {l.loop_counter_symbol: 0 for l in all_loops}

    for loop_node in inner_loops:
        loop_range = loop_node.stop - loop_node.start

        # cut off loop tail, that is not a multiple of four
        if assume_aligned and assume_sufficient_line_padding:
            loop_range = loop_node.stop - loop_node.start
            new_stop = loop_node.start + modulo_ceil(loop_range, vector_width)
            loop_node.stop = new_stop
        else:
            cutting_point = modulo_floor(loop_range, vector_width) + loop_node.start
            loop_nodes = [l for l in cut_loop(loop_node, [cutting_point]).args if isinstance(l, ast.LoopOverCoordinate)]
            assert len(loop_nodes) in (0, 1, 2)  # 2 for main and tail loop, 1 if loop range divisible by vector width
            if len(loop_nodes) == 0:
                continue
            loop_node = loop_nodes[0]
        
        # Find all array accesses (indexed) that depend on the loop counter as offset
        loop_counter_symbol = ast.LoopOverCoordinate.get_loop_counter_symbol(loop_node.coordinate_to_loop_over)
        substitutions = {}
        successful = True
        for indexed in loop_node.atoms(sp.Indexed):
            base, index = indexed.args
            if loop_counter_symbol in index.atoms(sp.Symbol):
                loop_counter_is_offset = loop_counter_symbol not in (index - loop_counter_symbol).atoms()
                aligned_access = (index - loop_counter_symbol).subs(zero_loop_counters) == 0
                if not loop_counter_is_offset:
                    successful = False
                    break
                typed_symbol = base.label
                assert type(typed_symbol.dtype) is PointerType, \
                    "Type of access is {}, {}".format(typed_symbol.dtype, indexed)

                vec_type = VectorType(typed_symbol.dtype.base_type, vector_width)
                use_aligned_access = aligned_access and assume_aligned
                nontemporal = False
                if hasattr(indexed, 'field'):
                    nontemporal = (indexed.field in nontemporal_fields) or (indexed.field.name in nontemporal_fields)
                substitutions[indexed] = vector_memory_access(indexed, vec_type, use_aligned_access, nontemporal)
        if not successful:
            warnings.warn("Could not vectorize loop because of non-consecutive memory access")
            continue

        loop_node.step = vector_width
        loop_node.subs(substitutions)


def insert_vector_casts(ast_node):
    """Inserts necessary casts from scalar values to vector values."""

    handled_functions = (sp.Add, sp.Mul, fast_division, fast_sqrt, fast_inv_sqrt, vec_any, vec_all)

    def visit_expr(expr):

        if isinstance(expr, cast_func) or isinstance(expr, vector_memory_access):
            return expr
        elif expr.func in handled_functions or isinstance(expr, sp.Rel) or isinstance(expr, sp.boolalg.BooleanFunction):
            new_args = [visit_expr(a) for a in expr.args]
            arg_types = [get_type_of_expression(a) for a in new_args]
            if not any(type(t) is VectorType for t in arg_types):
                return expr
            else:
                target_type = collate_types(arg_types)
                casted_args = [cast_func(a, target_type) if t != target_type else a
                               for a, t in zip(new_args, arg_types)]
                return expr.func(*casted_args)
        elif expr.func is sp.Pow:
            new_arg = visit_expr(expr.args[0])
            return expr.func(new_arg, expr.args[1])
        elif expr.func == sp.Piecewise:
            new_results = [visit_expr(a[0]) for a in expr.args]
            new_conditions = [visit_expr(a[1]) for a in expr.args]
            types_of_results = [get_type_of_expression(a) for a in new_results]
            types_of_conditions = [get_type_of_expression(a) for a in new_conditions]

            result_target_type = get_type_of_expression(expr)
            condition_target_type = collate_types(types_of_conditions)
            if type(condition_target_type) is VectorType and type(result_target_type) is not VectorType:
                result_target_type = VectorType(result_target_type, width=condition_target_type.width)
            if type(condition_target_type) is not VectorType and type(result_target_type) is VectorType:
                condition_target_type = VectorType(condition_target_type, width=result_target_type.width)

            casted_results = [cast_func(a, result_target_type) if t != result_target_type else a
                              for a, t in zip(new_results, types_of_results)]

            casted_conditions = [cast_func(a, condition_target_type)
                                 if t != condition_target_type and a is not True else a
                                 for a, t in zip(new_conditions, types_of_conditions)]

            return sp.Piecewise(*[(r, c) for r, c in zip(casted_results, casted_conditions)])
        else:
            return expr

    def visit_node(node, substitution_dict):
        substitution_dict = substitution_dict.copy()
        for arg in node.args:
            if isinstance(arg, ast.SympyAssignment):
                assignment = arg
                subs_expr = fast_subs(assignment.rhs, substitution_dict,
                                      skip=lambda e: isinstance(e, ast.ResolvedFieldAccess))
                assignment.rhs = visit_expr(subs_expr)
                rhs_type = get_type_of_expression(assignment.rhs)
                if isinstance(assignment.lhs, TypedSymbol):
                    lhs_type = assignment.lhs.dtype
                    if type(rhs_type) is VectorType and type(lhs_type) is not VectorType:
                        new_lhs_type = VectorType(lhs_type, rhs_type.width)
                        new_lhs = TypedSymbol(assignment.lhs.name, new_lhs_type)
                        substitution_dict[assignment.lhs] = new_lhs
                        assignment.lhs = new_lhs
                elif isinstance(assignment.lhs.func, cast_func):
                    lhs_type = assignment.lhs.args[1]
                    if type(lhs_type) is VectorType and type(rhs_type) is not VectorType:
                        assignment.rhs = cast_func(assignment.rhs, lhs_type)
            elif isinstance(arg, ast.Conditional):
                arg.condition_expr = fast_subs(arg.condition_expr, substitution_dict,
                                               skip=lambda e: isinstance(e, ast.ResolvedFieldAccess))
                arg.condition_expr = visit_expr(arg.condition_expr)
                visit_node(arg, substitution_dict)
            else:
                visit_node(arg, substitution_dict)

    visit_node(ast_node, {})
