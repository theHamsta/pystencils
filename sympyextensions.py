import itertools
import warnings
import operator
from functools import reduce, partial
from collections import defaultdict, Counter
import sympy as sp
from sympy.functions import Abs
from typing import Optional, Union, List, TypeVar, Iterable, Sequence, Callable, Dict, Tuple

from pystencils.data_types import get_type_of_expression, get_base_type
from pystencils.assignment import Assignment

T = TypeVar('T')


def prod(seq: Iterable[T]) -> T:
    """Takes a sequence and returns the product of all elements"""
    return reduce(operator.mul, seq, 1)


def is_integer_sequence(sequence: Iterable) -> bool:
    """Checks if all elements of the passed sequence can be cast to integers"""
    try:
        for i in sequence:
            int(i)
        return True
    except TypeError:
        return False


def scalar_product(a: Iterable[T], b: Iterable[T]) -> T:
    """Scalar product between two sequences."""
    return sum(a_i * b_i for a_i, b_i in zip(a, b))


def kronecker_delta(*args):
    """Kronecker delta for variable number of arguments, 1 if all args are equal, otherwise 0"""
    for a in args:
        if a != args[0]:
            return 0
    return 1


def multidimensional_sum(i, dim):
    """Multidimensional summation

    Example:
        >>> list(multidimensional_sum(2, dim=3))
        [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (2, 0), (2, 1), (2, 2)]
    """
    prod_args = [range(dim)] * i
    return itertools.product(*prod_args)


def normalize_product(product: sp.Expr) -> List[sp.Expr]:
    """Expects a sympy expression that can be interpreted as a product and returns a list of all factors.

    Removes sp.Pow nodes that have integer exponent by representing them as single factors in list.

    Returns:
        * for a Mul node list of factors ('args')
        * for a Pow node with positive integer exponent a list of factors
        * for other node types [product] is returned
    """
    def handle_pow(power):
        if power.exp.is_integer and power.exp.is_number and power.exp > 0:
            return [power.base] * power.exp
        else:
            return [power]

    if isinstance(product, sp.Pow):
        return handle_pow(product)
    elif isinstance(product, sp.Mul):
        result = []
        for a in product.args:
            if a.func == sp.Pow:
                result += handle_pow(a)
            else:
                result.append(a)
        return result
    else:
        return [product]


def symmetric_product(*args, with_diagonal: bool = True) -> Iterable:
    """Similar to itertools.product but yields only values where the index is ascending i.e. values below/up to diagonal

    Examples:
        >>> list(symmetric_product([1, 2, 3], ['a', 'b', 'c']))
        [(1, 'a'), (1, 'b'), (1, 'c'), (2, 'b'), (2, 'c'), (3, 'c')]
        >>> list(symmetric_product([1, 2, 3], ['a', 'b', 'c'], with_diagonal=False))
        [(1, 'b'), (1, 'c'), (2, 'c')]
    """
    ranges = [range(len(a)) for a in args]
    for idx in itertools.product(*ranges):
        valid_index = True
        for t in range(1, len(idx)):
            if (with_diagonal and idx[t - 1] > idx[t]) or (not with_diagonal and idx[t - 1] >= idx[t]):
                valid_index = False
                break
        if valid_index:
            yield tuple(a[i] for a, i in zip(args, idx))


def fast_subs(expression: T, substitutions: Dict,
              skip: Optional[Callable[[sp.Expr], bool]] = None) -> T:
    """Similar to sympy subs function.

    Args:
        expression: expression where parts should be substituted
        substitutions: dict defining substitutions by mapping from old to new terms
        skip: function that marks expressions to be skipped (if True is returned) - that means that in these skipped
              expressions no substitutions are done

    This version is much faster for big substitution dictionaries than sympy version
    """
    if type(expression) is sp.Matrix:
        return expression.copy().applyfunc(partial(fast_subs, substitutions=substitutions))

    def visit(expr):
        if skip and skip(expr):
            return expr
        if hasattr(expr, "fast_subs"):
            return expr.fast_subs(substitutions)
        if expr in substitutions:
            return substitutions[expr]
        if not hasattr(expr, 'args'):
            return expr
        param_list = [visit(a) for a in expr.args]
        return expr if not param_list else expr.func(*param_list)

    if len(substitutions) == 0:
        return expression
    else:
        return visit(expression)


def fast_subs_and_normalize(expression, substitutions: Dict[sp.Expr, sp.Expr],
                            normalize: Callable[[sp.Expr], sp.Expr]) -> sp.Expr:
    """Similar to fast_subs, but calls a normalization function on all substituted terms to save one AST traversal."""

    def visit(expr):
        if expr in substitutions:
            return substitutions[expr], True
        if not hasattr(expr, 'args'):
            return expr, False

        param_list = []
        substituted = False
        for a in expr.args:
            replaced_expr, s = visit(a)
            param_list.append(replaced_expr)
            if s:
                substituted = True

        if not param_list:
            return expr, False
        else:
            if substituted:
                result, _ = visit(normalize(expr.func(*param_list)))
                return result, True
            else:
                return expr.func(*param_list), False

    if len(substitutions) == 0:
        return expression
    else:
        res, _ = visit(expression)
        return res


def subs_additive(expr: sp.Expr, replacement: sp.Expr, subexpression: sp.Expr,
                  required_match_replacement: Optional[Union[int, float]] = 0.5,
                  required_match_original: Optional[Union[int, float]] = None) -> sp.Expr:
    """Transformation for replacing a given subexpression inside a sum.

    Examples:
        The next example demonstrates the advantage of replace_additive compared to sympy.subs:
        >>> x, y, z, k = sp.symbols("x y z k")
        >>> subs_additive(3*x + 3*y, replacement=k, subexpression=x + y)
        3*k

        Terms that don't match completely can be substituted at the cost of additional terms.
        This trade-off is managed using the required_match parameters.
        >>> subs_additive(3*x + 3*y + z, replacement=k, subexpression=x+y+z, required_match_original=1.0)
        3*x + 3*y + z
        >>> subs_additive(3*x + 3*y + z, replacement=k, subexpression=x+y+z, required_match_original=0.5)
        3*k - 2*z

    Args:
        expr: input expression
        replacement: expression that is inserted for subexpression (if found)
        subexpression: expression to replace
        required_match_replacement:
             * if float: the percentage of terms of the subexpression that has to be matched in order to replace
             * if integer: the total number of terms that has to be matched in order to replace
             * None: is equal to integer 1
             * if both match parameters are given, both restrictions have to be fulfilled (i.e. logical AND)
        required_match_original:
             * if float: the percentage of terms of the original addition expression that has to be matched
             * if integer: the total number of terms that has to be matched in order to replace
             * None: is equal to integer 1

    Returns:
        new expression with replacement
    """
    def normalize_match_parameter(match_parameter, expression_length):
        if match_parameter is None:
            return 1
        elif isinstance(match_parameter, float):
            assert 0 <= match_parameter <= 1
            res = int(match_parameter * expression_length)
            return max(res, 1)
        elif isinstance(match_parameter, int):
            assert match_parameter > 0
            return match_parameter
        raise ValueError("Invalid parameter")

    normalized_replacement_match = normalize_match_parameter(required_match_replacement, len(subexpression.args))

    def visit(current_expr):
        if current_expr.is_Add:
            expr_max_length = max(len(current_expr.args), len(subexpression.args))
            normalized_current_expr_match = normalize_match_parameter(required_match_original, expr_max_length)
            expr_coefficients = current_expr.as_coefficients_dict()
            subexpression_coefficient_dict = subexpression.as_coefficients_dict()
            intersection = set(subexpression_coefficient_dict.keys()).intersection(set(expr_coefficients))
            if len(intersection) >= max(normalized_replacement_match, normalized_current_expr_match):
                # find common factor
                factors = defaultdict(lambda: 0)
                skips = 0
                for common_symbol in subexpression_coefficient_dict.keys():
                    if common_symbol not in expr_coefficients:
                        skips += 1
                        continue
                    factor = expr_coefficients[common_symbol] / subexpression_coefficient_dict[common_symbol]
                    factors[sp.simplify(factor)] += 1

                common_factor = max(factors.items(), key=operator.itemgetter(1))[0]
                if factors[common_factor] >= max(normalized_current_expr_match, normalized_replacement_match):
                    return current_expr - common_factor * subexpression + common_factor * replacement

        # if no subexpression was found
        param_list = [visit(a) for a in current_expr.args]
        if not param_list:
            return current_expr
        else:
            return current_expr.func(*param_list, evaluate=False)

    return visit(expr)


def replace_second_order_products(expr: sp.Expr, search_symbols: Iterable[sp.Symbol],
                                  positive: Optional[bool] = None,
                                  replace_mixed: Optional[List[Assignment]] = None) -> sp.Expr:
    """Replaces second order mixed terms like x*y by 2*( (x+y)**2 - x**2 - y**2 ).

    This makes the term longer - simplify usually is undoing these - however this
    transformation can be done to find more common sub-expressions

    Args:
        expr: input expression
        search_symbols: symbols that are searched for
                         for example, given [x,y,z] terms like x*y, x*z, z*y are replaced
        positive: there are two ways to do this substitution, either with term
                 (x+y)**2 or (x-y)**2 . if positive=True the first version is done,
                 if positive=False the second version is done, if positive=None the
                 sign is determined by the sign of the mixed term that is replaced
        replace_mixed: if a list is passed here, the expr x+y or x-y is replaced by a special new symbol
                       and the replacement equation is added to the list
    """
    mixed_symbols_replaced = set([e.lhs for e in replace_mixed]) if replace_mixed is not None else set()

    if expr.is_Mul:
        distinct_search_symbols = set()
        nr_of_search_terms = 0
        other_factors = 1
        for t in expr.args:
            if t in search_symbols:
                nr_of_search_terms += 1
                distinct_search_symbols.add(t)
            else:
                other_factors *= t
        if len(distinct_search_symbols) == 2 and nr_of_search_terms == 2:
            u, v = sorted(list(distinct_search_symbols), key=lambda symbol: symbol.name)
            if positive is None:
                other_factors_without_symbols = other_factors
                for s in other_factors.atoms(sp.Symbol):
                    other_factors_without_symbols = other_factors_without_symbols.subs(s, 1)
                positive = other_factors_without_symbols.is_positive
                assert positive is not None
            sign = 1 if positive else -1
            if replace_mixed is not None:
                new_symbol_str = 'P' if positive else 'M'
                mixed_symbol_name = u.name + new_symbol_str + v.name
                mixed_symbol = sp.Symbol(mixed_symbol_name.replace("_", ""))
                if mixed_symbol not in mixed_symbols_replaced:
                    mixed_symbols_replaced.add(mixed_symbol)
                    replace_mixed.append(Assignment(mixed_symbol, u + sign * v))
            else:
                mixed_symbol = u + sign * v
            return sp.Rational(1, 2) * sign * other_factors * (mixed_symbol ** 2 - u ** 2 - v ** 2)

    param_list = [replace_second_order_products(a, search_symbols, positive, replace_mixed) for a in expr.args]
    result = expr.func(*param_list, evaluate=False) if param_list else expr
    return result


def remove_higher_order_terms(expr: sp.Expr, symbols: Sequence[sp.Symbol], order: int = 3) -> sp.Expr:
    """Removes all terms that contain more than 'order' factors of given 'symbols'

    Example:
        >>> x, y = sp.symbols("x y")
        >>> term = x**2 * y + y**2 * x + y**3 + x + y ** 2
        >>> remove_higher_order_terms(term, order=2, symbols=[x, y])
        x + y**2
    """
    from sympy.core.power import Pow
    from sympy.core.add import Add, Mul

    result = 0
    expr = expr.expand()

    def velocity_factors_in_product(product):
        factor_count = 0
        if type(product) is Mul:
            for factor in product.args:
                if type(factor) == Pow:
                    if factor.args[0] in symbols:
                        factor_count += factor.args[1]
                if factor in symbols:
                    factor_count += 1
        elif type(product) is Pow:
            if product.args[0] in symbols:
                factor_count += product.args[1]
        return factor_count

    if type(expr) == Mul or type(expr) == Pow:
        if velocity_factors_in_product(expr) <= order:
            return expr
        else:
            return sp.Rational(0, 1)

    if type(expr) != Add:
        return expr

    for sum_term in expr.args:
        if velocity_factors_in_product(sum_term) <= order:
            result += sum_term
    return result


def complete_the_square(expr: sp.Expr, symbol_to_complete: sp.Symbol,
                        new_variable: sp.Symbol) -> Tuple[sp.Expr, Optional[Tuple[sp.Symbol, sp.Expr]]]:
    """Transforms second order polynomial into only squared part.

    Examples:
        >>> a, b, c, s, n = sp.symbols("a b c s n")
        >>> expr = a * s**2 + b * s + c
        >>> completed_expr, substitution = complete_the_square(expr, symbol_to_complete=s, new_variable=n)
        >>> completed_expr
        a*n**2 + c - b**2/(4*a)
        >>> substitution
        (n, s + b/(2*a))

    Returns:
        (replaced_expr, tuple to pass to subs, such that old expr comes out again)
    """
    p = sp.Poly(expr, symbol_to_complete)
    coefficients = p.all_coeffs()
    if len(coefficients) != 3:
        return expr, None
    a, b, _ = coefficients
    expr = expr.subs(symbol_to_complete, new_variable - b / (2 * a))
    return sp.simplify(expr), (new_variable, symbol_to_complete + b / (2 * a))


def complete_the_squares_in_exp(expr: sp.Expr, symbols_to_complete: Sequence[sp.Symbol]):
    """Completes squares in arguments of exponential which makes them simpler to integrate.

    Very useful for integrating Maxwell-Boltzmann equilibria and its moment generating function
    """
    dummies = [sp.Dummy() for _ in symbols_to_complete]

    def visit(term):
        if term.func == sp.exp:
            exp_arg = term.args[0]
            for symbol_to_complete, dummy in zip(symbols_to_complete, dummies):
                exp_arg, substitution = complete_the_square(exp_arg, symbol_to_complete, dummy)
            return sp.exp(sp.expand(exp_arg))
        else:
            param_list = [visit(a) for a in term.args]
            if not param_list:
                return term
            else:
                return term.func(*param_list)

    result = visit(expr)
    for s, d in zip(symbols_to_complete, dummies):
        result = result.subs(d, s)
    return result


def pow2mul(expr):
    """Convert integer powers in an expression to Muls, like a**2 => a*a. """
    powers = list(expr.atoms(sp.Pow))
    if any(not e.is_Integer for b, e in (i.as_base_exp() for i in powers)):
        raise ValueError("A power contains a non-integer exponent")
    substitutions = zip(powers, (sp.Mul(*[b]*e, evaluate=False) for b, e in (i.as_base_exp() for i in powers)))
    return expr.subs(substitutions)


def extract_most_common_factor(term):
    """Processes a sum of fractions: determines the most common factor and splits term in common factor and rest"""
    coefficient_dict = term.as_coefficients_dict()
    counter = Counter([Abs(v) for v in coefficient_dict.values()])
    common_factor, occurrences = max(counter.items(), key=operator.itemgetter(1))
    if occurrences == 1 and (1 in counter):
        common_factor = 1
    return common_factor, term / common_factor


def count_operations(term: Union[sp.Expr, List[sp.Expr]],
                     only_type: Optional[str] = 'real') -> Dict[str, int]:
    """Counts the number of additions, multiplications and division.

    Args:
        term: a sympy expression (term, assignment) or sequence of sympy objects
        only_type: 'real' or 'int' to count only operations on these types, or None for all

    Returns:
        dict with 'adds', 'muls' and 'divs' keys
    """
    result = {'adds': 0, 'muls': 0, 'divs': 0}

    if isinstance(term, Sequence):
        for element in term:
            r = count_operations(element, only_type)
            for operation_name in result.keys():
                result[operation_name] += r[operation_name]
        return result
    elif isinstance(term, Assignment):
        term = term.rhs

    term = term.evalf()

    def check_type(e):
        if only_type is None:
            return True
        try:
            base_type = get_base_type(get_type_of_expression(e))
        except ValueError:
            return False
        if only_type == 'int' and (base_type.is_int() or base_type.is_uint()):
            return True
        if only_type == 'real' and (base_type.is_float()):
            return True
        else:
            return base_type == only_type

    def visit(t):
        visit_children = True
        if t.func is sp.Add:
            if check_type(t):
                result['adds'] += len(t.args) - 1
        elif t.func is sp.Mul:
            if check_type(t):
                result['muls'] += len(t.args) - 1
                for a in t.args:
                    if a == 1 or a == -1:
                        result['muls'] -= 1
        elif t.func is sp.Float:
            pass
        elif isinstance(t, sp.Symbol):
            visit_children = False
        elif isinstance(t, sp.tensor.Indexed):
            visit_children = False
        elif t.is_integer:
            pass
        elif t.func is sp.Pow:
            if check_type(t.args[0]):
                visit_children = False
                if t.exp.is_integer and t.exp.is_number:
                    if t.exp >= 0:
                        result['muls'] += int(t.exp) - 1
                    else:
                        result['muls'] -= 1
                        result['divs'] += 1
                        result['muls'] += (-int(t.exp)) - 1
            else:
                warnings.warn("Counting operations: only integer exponents are supported in Pow, "
                              "counting will be inaccurate")
        else:
            warnings.warn("Unknown sympy node of type " + str(t.func) + " counting will be inaccurate")

        if visit_children:
            for a in t.args:
                visit(a)

    visit(term)
    return result


def count_operations_in_ast(ast) -> Dict[str, int]:
    """Counts number of operations in an abstract syntax tree, see also :func:`count_operations`"""
    from pystencils.astnodes import SympyAssignment
    result = {'adds': 0, 'muls': 0, 'divs': 0}

    def visit(node):
        if isinstance(node, SympyAssignment):
            r = count_operations(node.rhs)
            result['adds'] += r['adds']
            result['muls'] += r['muls']
            result['divs'] += r['divs']
        else:
            for arg in node.args:
                visit(arg)
    visit(ast)
    return result


def common_denominator(expr: sp.Expr) -> sp.Expr:
    """Finds least common multiple of all denominators occurring in an expression"""
    denominators = [r.q for r in expr.atoms(sp.Rational)]
    return sp.lcm(denominators)


def get_symmetric_part(expr: sp.Expr, symbols: Iterable[sp.Symbol]) -> sp.Expr:
    """
    Returns the symmetric part of a sympy expressions.

    Args:
        expr: sympy expression, labeled here as :math:`f`
        symbols: sequence of symbols which are considered as degrees of freedom, labeled here as :math:`x_0, x_1,...`

    Returns:
        :math:`\frac{1}{2} [ f(x_0, x_1, ..) + f(-x_0, -x_1) ]`
    """
    substitution_dict = {e: -e for e in symbols}
    return sp.Rational(1, 2) * (expr + expr.subs(substitution_dict))


def sort_assignments_topologically(assignments: Sequence[Assignment]) -> List[Assignment]:
    """Sorts assignments in topological order, such that symbols used on rhs occur first on a lhs"""
    res = sp.cse_main.reps_toposort([[e.lhs, e.rhs] for e in assignments])
    return [Assignment(a, b) for a, b in res]


def assignments_from_python_function(func, **kwargs):
    """
    Mechanism to simplify the generation of a list of sympy equations. 
    Introduces a special "assignment operator" written as "@=". Each line containing this operator gives an
    equation in the result list. Note that executing this function normally yields an error.
    
    Additionally the shortcut object 'S' is available to quickly create new sympy symbols.
    
    Example:
        
    >>> def my_kernel(s):
    ...     from pystencils import Field
    ...     f = Field.create_generic('f', spatial_dimensions=2, index_dimensions=0)
    ...     g = f.new_field_with_different_name('g')
    ...     
    ...     s.neighbors @= f[0,1] + f[1,0]
    ...     g[0,0]      @= s.neighbors + f[0,0]
    >>> assignments_from_python_function(my_kernel)
    [Assignment(neighbors, f_E + f_N), Assignment(g_C, f_C + neighbors)]
    """
    import inspect
    import re

    assignment_regexp = re.compile(r'(\s*)(.+?)@=(.*)')
    whitespace_regexp = re.compile(r'(\s*)(.*)')
    source_lines = inspect.getsourcelines(func)[0]

    # determine indentation
    first_code_line = source_lines[1]
    match_res = whitespace_regexp.match(first_code_line)
    assert match_res, "First line is not indented"
    num_whitespaces = len(match_res.group(1))

    for i in range(1, len(source_lines)):
        source_line = source_lines[i][num_whitespaces:]
        if 'return' in source_line:
            raise ValueError("Function may not have a return statement!")
        match_res = assignment_regexp.match(source_line)
        if match_res:
            source_line = "%s_result.append(Assignment(%s, %s))\n" % tuple(match_res.groups()[i] for i in range(3))
        source_lines[i] = source_line

    code = "".join(source_lines[1:])
    result = []
    locals_dict = {'_result': result,
                   'Assignment': Assignment,
                   's': SymbolCreator()}
    locals_dict.update(kwargs)
    globals_dict = inspect.stack()[1][0].f_globals.copy()
    globals_dict.update(inspect.stack()[1][0].f_locals)

    exec(code, globals_dict, locals_dict)
    return result


class SymbolCreator:
    def __getattribute__(self, name):
        return sp.Symbol(name)
