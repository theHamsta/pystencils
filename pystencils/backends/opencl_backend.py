from os.path import dirname, join

import pystencils.data_types
from pystencils.astnodes import Node
from pystencils.backends.cbackend import CustomSympyPrinter, generate_c
from pystencils.backends.cuda_backend import CudaBackend, CudaSympyPrinter
from pystencils.fast_approximation import fast_division, fast_inv_sqrt, fast_sqrt

with open(join(dirname(__file__), 'opencl1.1_known_functions.txt')) as f:
    lines = f.readlines()
    OPENCL_KNOWN_FUNCTIONS = {l.strip(): l.strip() for l in lines if l}


def generate_opencl(astnode: Node, signature_only: bool = False) -> str:
    """Prints an abstract syntax tree node (made for target 'gpu') as OpenCL code.

    Args:
        astnode: KernelFunction node to generate code for
        signature_only: if True only the signature is printed

    Returns:
        C-like code for the ast node and its descendants
    """
    return generate_c(astnode, signature_only, dialect='opencl')


class OpenClBackend(CudaBackend):

    def __init__(self,
                 sympy_printer=None,
                 signature_only=False):
        if not sympy_printer:
            sympy_printer = OpenClSympyPrinter()

        super().__init__(sympy_printer, signature_only)
        self._dialect = 'opencl'

    def _print_Type(self, node):
        code = super()._print_Type(node)
        if isinstance(node, pystencils.data_types.PointerType):
            return "__global " + code
        else:
            return code

    def _print_ThreadBlockSynchronization(self, node):
        raise NotImplementedError()

    def _print_TextureDeclaration(self, node):
        raise NotImplementedError()


class OpenClSympyPrinter(CudaSympyPrinter):
    language = "OpenCL"

    DIMENSION_MAPPING = {
        'x': '0',
        'y': '1',
        'z': '2'
    }
    INDEXING_FUNCTION_MAPPING = {
        'blockIdx': 'get_group_id',
        'threadIdx': 'get_local_id',
        'blockDim': 'get_local_size',
        'gridDim': 'get_global_size'
    }

    def __init__(self):
        CustomSympyPrinter.__init__(self)
        self.known_functions = OPENCL_KNOWN_FUNCTIONS

    def _print_ThreadIndexingSymbol(self, node):
        symbol_name: str = node.name
        function_name, dimension = tuple(symbol_name.split("."))
        dimension = self.DIMENSION_MAPPING[dimension]
        function_name = self.INDEXING_FUNCTION_MAPPING[function_name]
        return f"{function_name}({dimension})"

    def _print_TextureAccess(self, node):
        raise NotImplementedError()

    # For math functions, OpenCL is more similar to the C++ printer CustomSympyPrinter
    # since built-in math functions are generic.
    # In CUDA, you have to differentiate between `sin` and `sinf`
    _print_math_func = CustomSympyPrinter._print_math_func
    _print_Pow = CustomSympyPrinter._print_Pow

    def _print_Function(self, expr):
        if isinstance(expr, fast_division):
            return "native_divide(%s, %s)" % tuple(self._print(a) for a in expr.args)
        elif isinstance(expr, fast_sqrt):
            return "native_sqrt(%s)" % tuple(self._print(a) for a in expr.args)
        elif isinstance(expr, fast_inv_sqrt):
            return "native_rsqrt(%s)" % tuple(self._print(a) for a in expr.args)
        return CustomSympyPrinter._print_Function(self, expr)
