import textwrap
from sympy.utilities.codegen import CCodePrinter
from pystencils.ast import Node


def generateC(astNode):
    """
    Prints the abstract syntax tree as C function
    """
    printer = CBackend(cuda=False)
    return printer(astNode)


def generateCUDA(astNode):
    printer = CBackend(cuda=True)
    return printer(astNode)

# --------------------------------------- Backend Specific Nodes -------------------------------------------------------


class CustomCppCode(Node):
    def __init__(self, code, symbolsRead, symbolsDefined):
        self._code = "\n" + code
        self._symbolsRead = set(symbolsRead)
        self._symbolsDefined = set(symbolsDefined)

    @property
    def code(self):
        return self._code

    @property
    def args(self):
        return []

    @property
    def symbolsDefined(self):
        return self._symbolsDefined

    @property
    def undefinedSymbols(self):
        return self.symbolsDefined - self._symbolsRead


class PrintNode(CustomCppCode):
    def __init__(self, symbolToPrint):
        code = '\nstd::cout << "%s  =  " << %s << std::endl; \n' % (symbolToPrint.name, symbolToPrint.name)
        super(PrintNode, self).__init__(code, symbolsRead=[symbolToPrint], symbolsDefined=set())


# ------------------------------------------- Printer ------------------------------------------------------------------


class CBackend:

    def __init__(self, cuda=False, sympyPrinter=None):
        self.cuda = cuda
        if sympyPrinter is None:
            self.sympyPrinter = CustomSympyPrinter()
        else:
            self.sympyPrinter = sympyPrinter

        self._indent = "   "

    def __call__(self, node):
        return str(self._print(node))

    def _print(self, node):
        for cls in type(node).__mro__:
            methodName = "_print_" + cls.__name__
            if hasattr(self, methodName):
                return getattr(self, methodName)(node)
        raise NotImplementedError("CBackend does not support node of type " + cls.__name__)

    def _print_KernelFunction(self, node):
        functionArguments = ["%s %s" % (s.dtype, s.name) for s in node.parameters]
        prefix = "__global__ void" if self.cuda else "void"
        funcDeclaration = "%s %s(%s)" % (prefix, node.functionName, ", ".join(functionArguments))
        body = self._print(node.body)
        return funcDeclaration + "\n" + body

    def _print_Block(self, node):
        blockContents = "\n".join([self._print(child) for child in node.args])
        return "{\n%s\n}" % (textwrap.indent(blockContents, self._indent))

    def _print_PragmaBlock(self, node):
        return "%s\n%s" % (node.pragmaLine, self._print_Block(node))

    def _print_LoopOverCoordinate(self, node):
        counterVar = node.loopCounterName
        start = "int %s = %s" % (counterVar, self.sympyPrinter.doprint(node.start))
        condition = "%s < %s" % (counterVar, self.sympyPrinter.doprint(node.stop))
        update = "%s += %s" % (counterVar, self.sympyPrinter.doprint(node.step),)
        loopStr = "for (%s; %s; %s)" % (start, condition, update)

        prefix = "\n".join(node.prefixLines)
        if prefix:
            prefix += "\n"
        return "%s%s\n%s" % (prefix, loopStr, self._print(node.body))

    def _print_SympyAssignment(self, node):
        dtype = ""
        if node.isDeclaration:
            if node.isConst:
                dtype = "const " + node.lhs.dtype + " "
            else:
                dtype = node.lhs.dtype + " "
        return "%s %s = %s;" % (dtype, self.sympyPrinter.doprint(node.lhs), self.sympyPrinter.doprint(node.rhs))

    def _print_TemporaryMemoryAllocation(self, node):
        return "%s * %s = new %s[%s];" % (node.symbol.dtype, self.sympyPrinter.doprint(node.symbol),
                                         node.symbol.dtype, self.sympyPrinter.doprint(node.size))

    def _print_TemporaryMemoryFree(self, node):
        return "delete [] %s;" % (self.sympyPrinter.doprint(node.symbol),)

    def _print_CustomCppCode(self, node):
        return node.code


# ------------------------------------------ Helper function & classes -------------------------------------------------


class CustomSympyPrinter(CCodePrinter):
    def _print_Pow(self, expr):
        """Don't use std::pow function, for small integer exponents, write as multiplication"""
        if expr.exp.is_integer and expr.exp.is_number and 0 < expr.exp < 8:
            return '(' + '*'.join(["(" + self._print(expr.base) + ")"] * expr.exp) + ')'
        else:
            return super(CustomSympyPrinter, self)._print_Pow(expr)

    def _print_Rational(self, expr):
        """Evaluate all rationals i.e. print 0.25 instead of 1.0/4.0"""
        return str(expr.evalf().num)

    def _print_Equality(self, expr):
        """Equality operator is not printable in default printer"""
        return '((' + self._print(expr.lhs) + ") == (" + self._print(expr.rhs) + '))'

    def _print_Piecewise(self, expr):
        """Print piecewise in one line (remove newlines)"""
        result = super(CustomSympyPrinter, self)._print_Piecewise(expr)
        return result.replace("\n", "")