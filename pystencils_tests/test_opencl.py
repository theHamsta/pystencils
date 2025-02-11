import numpy as np
import pytest
import sympy as sp

import pystencils
from pystencils.backends.cuda_backend import CudaBackend
from pystencils.backends.opencl_backend import OpenClBackend
from pystencils.opencl.opencljit import make_python_function

try:
    import pyopencl as cl
    HAS_OPENCL = True
except Exception:
    HAS_OPENCL = False


def test_print_opencl():
    z, y, x = pystencils.fields("z, y, x: [2d]")

    assignments = pystencils.AssignmentCollection({
        z[0, 0]: x[0, 0] * sp.log(x[0, 0] * y[0, 0])
    })

    print(assignments)

    ast = pystencils.create_kernel(assignments, target='gpu')

    print(ast)

    code = pystencils.show_code(ast, custom_backend=CudaBackend())
    print(code)

    opencl_code = pystencils.show_code(ast, custom_backend=OpenClBackend())
    print(opencl_code)

    assert "__global double * RESTRICT const _data_x" in str(opencl_code)
    assert "__global double * RESTRICT" in str(opencl_code)
    assert "get_local_id(0)" in str(opencl_code)


@pytest.mark.skipif(not HAS_OPENCL, reason="Test requires pyopencl")
def test_opencl_jit_fixed_size():
    z, y, x = pystencils.fields("z, y, x: [20,30]")

    assignments = pystencils.AssignmentCollection({
        z[0, 0]: x[0, 0] * sp.log(x[0, 0] * y[0, 0])
    })

    print(assignments)

    ast = pystencils.create_kernel(assignments, target='gpu')

    print(ast)

    code = pystencils.show_code(ast, custom_backend=CudaBackend())
    print(code)
    opencl_code = pystencils.show_code(ast, custom_backend=OpenClBackend())
    print(opencl_code)

    cuda_kernel = ast.compile()
    assert cuda_kernel is not None

    import pycuda.gpuarray as gpuarray

    x_cpu = np.random.rand(20, 30)
    y_cpu = np.random.rand(20, 30)
    z_cpu = np.random.rand(20, 30)

    x = gpuarray.to_gpu(x_cpu)
    y = gpuarray.to_gpu(y_cpu)
    z = gpuarray.to_gpu(z_cpu)
    cuda_kernel(x=x, y=y, z=z)

    result_cuda = z.get()

    import pyopencl.array as array
    ctx = cl.create_some_context(0)
    queue = cl.CommandQueue(ctx)

    x = array.to_device(queue, x_cpu)
    y = array.to_device(queue, y_cpu)
    z = array.to_device(queue, z_cpu)

    opencl_kernel = make_python_function(ast, queue, ctx)
    assert opencl_kernel is not None
    opencl_kernel(x=x, y=y, z=z)

    result_opencl = z.get(queue)

    assert np.allclose(result_cuda, result_opencl)


@pytest.mark.skipif(not HAS_OPENCL, reason="Test requires pyopencl")
def test_opencl_jit():
    z, y, x = pystencils.fields("z, y, x: [2d]")

    assignments = pystencils.AssignmentCollection({
        z[0, 0]: x[0, 0] * sp.log(x[0, 0] * y[0, 0])
    })

    print(assignments)

    ast = pystencils.create_kernel(assignments, target='gpu')

    print(ast)

    code = pystencils.show_code(ast, custom_backend=CudaBackend())
    print(code)
    opencl_code = pystencils.show_code(ast, custom_backend=OpenClBackend())
    print(opencl_code)

    cuda_kernel = ast.compile()
    assert cuda_kernel is not None

    import pycuda.gpuarray as gpuarray

    x_cpu = np.random.rand(20, 30)
    y_cpu = np.random.rand(20, 30)
    z_cpu = np.random.rand(20, 30)

    x = gpuarray.to_gpu(x_cpu)
    y = gpuarray.to_gpu(y_cpu)
    z = gpuarray.to_gpu(z_cpu)
    cuda_kernel(x=x, y=y, z=z)

    result_cuda = z.get()

    import pyopencl.array as array
    ctx = cl.create_some_context(0)
    queue = cl.CommandQueue(ctx)

    x = array.to_device(queue, x_cpu)
    y = array.to_device(queue, y_cpu)
    z = array.to_device(queue, z_cpu)

    opencl_kernel = make_python_function(ast, queue, ctx)
    assert opencl_kernel is not None
    opencl_kernel(x=x, y=y, z=z)

    result_opencl = z.get(queue)

    assert np.allclose(result_cuda, result_opencl)


@pytest.mark.skipif(not HAS_OPENCL, reason="Test requires pyopencl")
def test_opencl_jit_with_parameter():
    z, y, x = pystencils.fields("z, y, x: [2d]")

    a = sp.Symbol('a')
    assignments = pystencils.AssignmentCollection({
        z[0, 0]: x[0, 0] * sp.log(x[0, 0] * y[0, 0]) + a
    })

    print(assignments)

    ast = pystencils.create_kernel(assignments, target='gpu')

    print(ast)

    code = pystencils.show_code(ast, custom_backend=CudaBackend())
    print(code)
    opencl_code = pystencils.show_code(ast, custom_backend=OpenClBackend())
    print(opencl_code)

    cuda_kernel = ast.compile()
    assert cuda_kernel is not None

    import pycuda.gpuarray as gpuarray

    x_cpu = np.random.rand(20, 30)
    y_cpu = np.random.rand(20, 30)
    z_cpu = np.random.rand(20, 30)

    x = gpuarray.to_gpu(x_cpu)
    y = gpuarray.to_gpu(y_cpu)
    z = gpuarray.to_gpu(z_cpu)
    cuda_kernel(x=x, y=y, z=z, a=5.)

    result_cuda = z.get()

    import pyopencl.array as array
    ctx = cl.create_some_context(0)
    queue = cl.CommandQueue(ctx)

    x = array.to_device(queue, x_cpu)
    y = array.to_device(queue, y_cpu)
    z = array.to_device(queue, z_cpu)

    opencl_kernel = make_python_function(ast, queue, ctx)
    assert opencl_kernel is not None
    opencl_kernel(x=x, y=y, z=z, a=5.)

    result_opencl = z.get(queue)

    assert np.allclose(result_cuda, result_opencl)


if __name__ == '__main__':
    test_opencl_jit()
