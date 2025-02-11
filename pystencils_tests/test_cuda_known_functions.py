import sympy

import pystencils
from pystencils.astnodes import get_dummy_symbol
from pystencils.backends.cuda_backend import CudaSympyPrinter
from pystencils.data_types import address_of


def test_cuda_known_functions():
    printer = CudaSympyPrinter()
    print(printer.known_functions)

    x, y = pystencils.fields('x,y: float32 [2d]')

    assignments = pystencils.AssignmentCollection({
        get_dummy_symbol(): sympy.Function('atomicAdd')(address_of(y.center()), 2),
        y.center():  sympy.Function('rsqrtf')(x[0, 0])
    })

    ast = pystencils.create_kernel(assignments, 'gpu')
    print(pystencils.show_code(ast))
    kernel = ast.compile()
    assert(kernel is not None)


def test_cuda_but_not_c():
    x, y = pystencils.fields('x,y: float32 [2d]')

    assignments = pystencils.AssignmentCollection({
        get_dummy_symbol(): sympy.Function('atomicAdd')(address_of(y.center()), 2),
        y.center():  sympy.Function('rsqrtf')(x[0, 0])
    })

    ast = pystencils.create_kernel(assignments, 'cpu')
    code = str(pystencils.show_code(ast))
    assert "Not supported" in code


def test_cuda_unknown():
    x, y = pystencils.fields('x,y: float32 [2d]')

    assignments = pystencils.AssignmentCollection({
        get_dummy_symbol(): sympy.Function('wtf')(address_of(y.center()), 2),
    })

    ast = pystencils.create_kernel(assignments, 'gpu')
    code = str(pystencils.show_code(ast))
    print(code)
    assert "Not supported in CUDA" in code
