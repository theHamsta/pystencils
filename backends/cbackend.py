import sympy as sp
from collections import namedtuple
from sympy.core import S
from typing import Optional, Set

try:
    from sympy.printing.ccode import C99CodePrinter as CCodePrinter
except ImportError:
    from sympy.printing.ccode import CCodePrinter  # for sympy versions < 1.1

from pystencils.integer_functions import bitwise_xor, bit_shift_right, bit_shift_left, bitwise_and, \
    bitwise_or, modulo_floor, modulo_ceil
from pystencils.astnodes import Node, ResolvedFieldAccess, KernelFunction
from pystencils.data_types import create_type, PointerType, get_type_of_expression, VectorType, cast_func, \
    vector_memory_access

__all__ = ['generate_c', 'CustomCppCode', 'PrintNode', 'get_headers', 'CustomSympyPrinter']


def generate_c(ast_node: Node, signature_only: bool = False, use_float_constants: Optional[bool] = None) -> str:
    """Prints an abstract syntax tree node as C or CUDA code.

    This function does not need to distinguish between C, C++ or CUDA code, it just prints 'C-like' code as encoded
    in the abstract syntax tree (AST). The AST is built differently for C or CUDA by calling different create_kernel
    functions.

    Args:
        ast_node:
        signature_only:
        use_float_constants:

    Returns:
        C-like code for the ast node and its descendants
    """
    if use_float_constants is None:
        field_types = set(o.field.dtype for o in ast_node.atoms(ResolvedFieldAccess))
        double = create_type('double')
        use_float_constants = double not in field_types

    printer = CBackend(constants_as_floats=use_float_constants, signature_only=signature_only,
                       vector_instruction_set=ast_node.instruction_set)
    return printer(ast_node)


def get_headers(ast_node: Node) -> Set[str]:
    """Return a set of header files, necessary to compile the printed C-like code."""
    headers = set()

    if isinstance(ast_node, KernelFunction) and ast_node.instruction_set:
        headers.update(ast_node.instruction_set['headers'])

    if hasattr(ast_node, 'headers'):
        headers.update(ast_node.headers)
    for a in ast_node.args:
        if isinstance(a, Node):
            headers.update(get_headers(a))

    return headers


# --------------------------------------- Backend Specific Nodes -------------------------------------------------------


class CustomCppCode(Node):
    def __init__(self, code, symbols_read, symbols_defined, parent=None):
        super(CustomCppCode, self).__init__(parent=parent)
        self._code = "\n" + code
        self._symbolsRead = set(symbols_read)
        self._symbolsDefined = set(symbols_defined)
        self.headers = []

    @property
    def code(self):
        return self._code

    @property
    def args(self):
        return []

    @property
    def symbols_defined(self):
        return self._symbolsDefined

    @property
    def undefined_symbols(self):
        return self.symbols_defined - self._symbolsRead


class PrintNode(CustomCppCode):
    # noinspection SpellCheckingInspection
    def __init__(self, symbol_to_print):
        code = '\nstd::cout << "%s  =  " << %s << std::endl; \n' % (symbol_to_print.name, symbol_to_print.name)
        super(PrintNode, self).__init__(code, symbols_read=[symbol_to_print], symbols_defined=set())
        self.headers.append("<iostream>")


# ------------------------------------------- Printer ------------------------------------------------------------------


# noinspection PyPep8Naming
class CBackend:

    def __init__(self, constants_as_floats=False, sympy_printer=None,
                 signature_only=False, vector_instruction_set=None):
        if sympy_printer is None:
            if vector_instruction_set is not None:
                self.sympy_printer = VectorizedCustomSympyPrinter(vector_instruction_set, constants_as_floats)
            else:
                self.sympy_printer = CustomSympyPrinter(constants_as_floats)
        else:
            self.sympy_printer = sympy_printer

        self._vectorInstructionSet = vector_instruction_set
        self._indent = "   "
        self._signatureOnly = signature_only

    def __call__(self, node):
        prev_is = VectorType.instruction_set
        VectorType.instruction_set = self._vectorInstructionSet
        result = str(self._print(node))
        VectorType.instruction_set = prev_is
        return result

    def _print(self, node):
        for cls in type(node).__mro__:
            method_name = "_print_" + cls.__name__
            if hasattr(self, method_name):
                return getattr(self, method_name)(node)

        raise NotImplementedError("CBackend does not support node of type " + str(type(node)))

    def _print_KernelFunction(self, node):
        function_arguments = ["%s %s" % (str(s.dtype), s.name) for s in node.parameters]
        func_declaration = "FUNC_PREFIX void %s(%s)" % (node.function_name, ", ".join(function_arguments))
        if self._signatureOnly:
            return func_declaration

        body = self._print(node.body)
        return func_declaration + "\n" + body

    def _print_Block(self, node):
        block_contents = "\n".join([self._print(child) for child in node.args])
        return "{\n%s\n}" % (self._indent + self._indent.join(block_contents.splitlines(True)))

    def _print_PragmaBlock(self, node):
        return "%s\n%s" % (node.pragma_line, self._print_Block(node))

    def _print_LoopOverCoordinate(self, node):
        counter_symbol = node.loop_counter_name
        start = "int %s = %s" % (counter_symbol, self.sympy_printer.doprint(node.start))
        condition = "%s < %s" % (counter_symbol, self.sympy_printer.doprint(node.stop))
        update = "%s += %s" % (counter_symbol, self.sympy_printer.doprint(node.step),)
        loop_str = "for (%s; %s; %s)" % (start, condition, update)

        prefix = "\n".join(node.prefix_lines)
        if prefix:
            prefix += "\n"
        return "%s%s\n%s" % (prefix, loop_str, self._print(node.body))

    def _print_SympyAssignment(self, node):
        if node.is_declaration:
            data_type = "const " + str(node.lhs.dtype) + " " if node.is_const else str(node.lhs.dtype) + " "
            return "%s%s = %s;" % (data_type, self.sympy_printer.doprint(node.lhs),
                                   self.sympy_printer.doprint(node.rhs))
        else:
            lhs_type = get_type_of_expression(node.lhs)
            if type(lhs_type) is VectorType and isinstance(node.lhs, cast_func):
                arg, data_type, aligned, nontemporal = node.lhs.args
                instr = 'storeU'
                if aligned:
                    instr = 'stream' if nontemporal else 'storeA'

                return self._vectorInstructionSet[instr].format("&" + self.sympy_printer.doprint(node.lhs.args[0]),
                                                                self.sympy_printer.doprint(node.rhs)) + ';'
            else:
                return "%s = %s;" % (self.sympy_printer.doprint(node.lhs), self.sympy_printer.doprint(node.rhs))

    def _print_TemporaryMemoryAllocation(self, node):
        align = 64
        np_dtype = node.symbol.dtype.base_type.numpy_dtype
        required_size = np_dtype.itemsize * node.size + align
        size = modulo_ceil(required_size, align)
        code = "{dtype} {name}=({dtype})aligned_alloc({align}, {size}) + {offset};"
        return code.format(dtype=node.symbol.dtype,
                           name=self.sympy_printer.doprint(node.symbol.name),
                           size=self.sympy_printer.doprint(size),
                           offset=int(node.offset(align)),
                           align=align)

    def _print_TemporaryMemoryFree(self, node):
        align = 64
        return "free(%s - %d);" % (self.sympy_printer.doprint(node.symbol.name), node.offset(align))

    @staticmethod
    def _print_CustomCppCode(node):
        return node.code

    def _print_Conditional(self, node):
        condition_expr = self.sympy_printer.doprint(node.condition_expr)
        true_block = self._print_Block(node.true_block)
        result = "if (%s)\n%s " % (condition_expr, true_block)
        if node.false_block:
            false_block = self._print_Block(node.false_block)
            result += "else " + false_block
        return result


# ------------------------------------------ Helper function & classes -------------------------------------------------


# noinspection PyPep8Naming
class CustomSympyPrinter(CCodePrinter):

    def __init__(self, constants_as_floats=False):
        self._constantsAsFloats = constants_as_floats
        super(CustomSympyPrinter, self).__init__()
        self._float_type = create_type("float32")

    def _print_Pow(self, expr):
        """Don't use std::pow function, for small integer exponents, write as multiplication"""
        if expr.exp.is_integer and expr.exp.is_number and 0 < expr.exp < 8:
            return "(" + self._print(sp.Mul(*[expr.base] * expr.exp, evaluate=False)) + ")"
        elif expr.exp.is_integer and expr.exp.is_number and - 8 < expr.exp < 0:
            return "1 / ({})".format(self._print(sp.Mul(*[expr.base] * (-expr.exp), evaluate=False)))
        else:
            return super(CustomSympyPrinter, self)._print_Pow(expr)

    def _print_Rational(self, expr):
        """Evaluate all rationals i.e. print 0.25 instead of 1.0/4.0"""
        res = str(expr.evalf().num)
        return res

    def _print_Equality(self, expr):
        """Equality operator is not printable in default printer"""
        return '((' + self._print(expr.lhs) + ") == (" + self._print(expr.rhs) + '))'

    def _print_Piecewise(self, expr):
        """Print piecewise in one line (remove newlines)"""
        result = super(CustomSympyPrinter, self)._print_Piecewise(expr)
        return result.replace("\n", "")

    def _print_Function(self, expr):
        function_map = {
            bitwise_xor: '^',
            bit_shift_right: '>>',
            bit_shift_left: '<<',
            bitwise_or: '|',
            bitwise_and: '&',
        }
        if hasattr(expr, 'to_c'):
            return expr.to_c(self._print)
        if expr.func == cast_func:
            arg, data_type = expr.args
            if isinstance(arg, sp.Number):
                return self._typed_number(arg, data_type)
            else:
                return "*((%s)(& %s))" % (PointerType(data_type), self._print(arg))
        elif expr.func == modulo_floor:
            assert all(get_type_of_expression(e).is_int() for e in expr.args)
            return "({dtype})({0} / {1}) * {1}".format(*expr.args, dtype=get_type_of_expression(expr.args[0]))
        elif expr.func in function_map:
            return "(%s %s %s)" % (self._print(expr.args[0]), function_map[expr.func], self._print(expr.args[1]))
        else:
            return super(CustomSympyPrinter, self)._print_Function(expr)

    def _typed_number(self, number, dtype):
        res = self._print(number)
        if dtype.is_float():
            if dtype == self._float_type:
                if '.' not in res:
                    res += ".0f"
                else:
                    res += "f"
            return res
        else:
            return res


# noinspection PyPep8Naming
class VectorizedCustomSympyPrinter(CustomSympyPrinter):
    SummandInfo = namedtuple("SummandInfo", ['sign', 'term'])

    def __init__(self, instruction_set, constants_as_floats=False):
        super(VectorizedCustomSympyPrinter, self).__init__(constants_as_floats)
        self.instruction_set = instruction_set

    def _scalarFallback(self, func_name, expr, *args, **kwargs):
        expr_type = get_type_of_expression(expr)
        if type(expr_type) is not VectorType:
            return getattr(super(VectorizedCustomSympyPrinter, self), func_name)(expr, *args, **kwargs)
        else:
            assert self.instruction_set['width'] == expr_type.width
            return None

    def _print_Function(self, expr):
        if expr.func == vector_memory_access:
            arg, data_type, aligned, _ = expr.args
            instruction = self.instruction_set['loadA'] if aligned else self.instruction_set['loadU']
            return instruction.format("& " + self._print(arg))
        elif expr.func == cast_func:
            arg, data_type = expr.args
            if type(data_type) is VectorType:
                return self.instruction_set['makeVec'].format(self._print(arg))

        return super(VectorizedCustomSympyPrinter, self)._print_Function(expr)

    def _print_And(self, expr):
        result = self._scalarFallback('_print_And', expr)
        if result:
            return result

        arg_strings = [self._print(a) for a in expr.args]
        assert len(arg_strings) > 0
        result = arg_strings[0]
        for item in arg_strings[1:]:
            result = self.instruction_set['&'].format(result, item)
        return result

    def _print_Or(self, expr):
        result = self._scalarFallback('_print_Or', expr)
        if result:
            return result

        arg_strings = [self._print(a) for a in expr.args]
        assert len(arg_strings) > 0
        result = arg_strings[0]
        for item in arg_strings[1:]:
            result = self.instruction_set['|'].format(result, item)
        return result

    def _print_Add(self, expr, order=None):
        result = self._scalarFallback('_print_Add', expr)
        if result:
            return result

        summands = []
        for term in expr.args:
            if term.func == sp.Mul:
                sign, t = self._print_Mul(term, inside_add=True)
            else:
                t = self._print(term)
                sign = 1
            summands.append(self.SummandInfo(sign, t))
        # Use positive terms first
        summands.sort(key=lambda e: e.sign, reverse=True)
        # if no positive term exists, prepend a zero
        if summands[0].sign == -1:
            summands.insert(0, self.SummandInfo(1, "0"))

        assert len(summands) >= 2
        processed = summands[0].term
        for summand in summands[1:]:
            func = self.instruction_set['-'] if summand.sign == -1 else self.instruction_set['+']
            processed = func.format(processed, summand.term)
        return processed

    def _print_Pow(self, expr):
        result = self._scalarFallback('_print_Pow', expr)
        if result:
            return result

        if expr.exp.is_integer and expr.exp.is_number and 0 < expr.exp < 8:
            return "(" + self._print(sp.Mul(*[expr.base] * expr.exp, evaluate=False)) + ")"
        elif expr.exp == -1:
            one = self.instruction_set['makeVec'].format(1.0)
            return self.instruction_set['/'].format(one, self._print(expr.base))
        elif expr.exp == 0.5:
            return self.instruction_set['sqrt'].format(self._print(expr.base))
        elif expr.exp.is_integer and expr.exp.is_number and - 8 < expr.exp < 0:
            one = self.instruction_set['makeVec'].format(1.0)
            return self.instruction_set['/'].format(one,
                                                    self._print(sp.Mul(*[expr.base] * (-expr.exp), evaluate=False)))
        else:
            raise ValueError("Generic exponential not supported: " + str(expr))

    def _print_Mul(self, expr, inside_add=False):
        # noinspection PyProtectedMember
        from sympy.core.mul import _keep_coeff

        result = self._scalarFallback('_print_Mul', expr)
        if result:
            return result

        c, e = expr.as_coeff_Mul()
        if c < 0:
            expr = _keep_coeff(-c, e)
            sign = -1
        else:
            sign = 1

        a = []  # items in the numerator
        b = []  # items that are in the denominator (if any)

        # Gather args for numerator/denominator
        for item in expr.as_ordered_factors():
            if item.is_commutative and item.is_Pow and item.exp.is_Rational and item.exp.is_negative:
                if item.exp != -1:
                    b.append(sp.Pow(item.base, -item.exp, evaluate=False))
                else:
                    b.append(sp.Pow(item.base, -item.exp))
            else:
                a.append(item)

        a = a or [S.One]

        a_str = [self._print(x) for x in a]
        b_str = [self._print(x) for x in b]

        result = a_str[0]
        for item in a_str[1:]:
            result = self.instruction_set['*'].format(result, item)

        if len(b) > 0:
            denominator_str = b_str[0]
            for item in b_str[1:]:
                denominator_str = self.instruction_set['*'].format(denominator_str, item)
            result = self.instruction_set['/'].format(result, denominator_str)

        if inside_add:
            return sign, result
        else:
            if sign < 0:
                return self.instruction_set['*'].format(self._print(S.NegativeOne), result)
            else:
                return result

    def _print_Relational(self, expr):
        result = self._scalarFallback('_print_Relational', expr)
        if result:
            return result
        return self.instruction_set[expr.rel_op].format(self._print(expr.lhs), self._print(expr.rhs))

    def _print_Equality(self, expr):
        result = self._scalarFallback('_print_Equality', expr)
        if result:
            return result
        return self.instruction_set['=='].format(self._print(expr.lhs), self._print(expr.rhs))

    def _print_Piecewise(self, expr):
        result = self._scalarFallback('_print_Piecewise', expr)
        if result:
            return result

        if expr.args[-1].cond.args[0] is not sp.sympify(True):
            # We need the last conditional to be a True, otherwise the resulting
            # function may not return a result.
            raise ValueError("All Piecewise expressions must contain an "
                             "(expr, True) statement to be used as a default "
                             "condition. Without one, the generated "
                             "expression may not evaluate to anything under "
                             "some condition.")

        result = self._print(expr.args[-1][0])
        for true_expr, condition in reversed(expr.args[:-1]):
            # noinspection SpellCheckingInspection
            result = self.instruction_set['blendv'].format(result, self._print(true_expr), self._print(condition))
        return result
