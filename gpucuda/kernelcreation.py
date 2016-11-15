from collections import defaultdict

import sympy as sp

from pystencils.transformations import resolveFieldAccesses, typeAllEquations, parseBasePointerInfo
from pystencils.ast import Block, KernelFunction
from pystencils import Field

BLOCK_IDX = list(sp.symbols("blockIdx.x blockIdx.y blockIdx.z"))
THREAD_IDX = list(sp.symbols("threadIdx.x threadIdx.y threadIdx.z"))


def getLinewiseCoordinates(field, ghostLayers):
    availableIndices = [THREAD_IDX[0]] + BLOCK_IDX
    assert field.spatialDimensions <= 4, "This indexing scheme supports at most 4 spatial dimensions"
    result = availableIndices[:field.spatialDimensions]

    fastestCoordinate = field.layout[-1]
    result[0], result[fastestCoordinate] = result[fastestCoordinate], result[0]

    def getCallParameters(arrShape):
        def getShapeOfCudaIdx(cudaIdx):
            if cudaIdx not in result:
                return 1
            else:
                return arrShape[result.index(cudaIdx)] - 2 * ghostLayers

        return {'block': tuple([getShapeOfCudaIdx(idx) for idx in THREAD_IDX]),
                'grid': tuple([getShapeOfCudaIdx(idx) for idx in BLOCK_IDX]) }

    return [i + ghostLayers for i in result], getCallParameters


def createCUDAKernel(listOfEquations, functionName="kernel", typeForSymbol=defaultdict(lambda: "double")):
    fieldsRead, fieldsWritten, assignments = typeAllEquations(listOfEquations, typeForSymbol)
    readOnlyFields = set([f.name for f in fieldsRead - fieldsWritten])

    code = KernelFunction(Block(assignments), fieldsRead.union(fieldsWritten), functionName)
    code.globalVariables.update(BLOCK_IDX + THREAD_IDX)

    fieldAccesses = code.atoms(Field.Access)
    requiredGhostLayers = max([fa.requiredGhostLayers for fa in fieldAccesses])

    coordMapping, getCallParameters = getLinewiseCoordinates(list(fieldsRead)[0], requiredGhostLayers)
    allFields = fieldsRead.union(fieldsWritten)
    basePointerInfo = [['spatialInner0']]
    basePointerInfos = {f.name: parseBasePointerInfo(basePointerInfo, [2, 1, 0], f) for f in allFields}
    resolveFieldAccesses(code, readOnlyFields, fieldToFixedCoordinates={'src': coordMapping, 'dst': coordMapping},
                         fieldToBasePointerInfo=basePointerInfos)
    # add the function which determines #blocks and #threads as additional member to KernelFunction node
    # this is used by the jit
    code.getCallParameters = getCallParameters
    return code


if __name__ == "__main__":
    import sympy as sp
    from lbmpy.stencils import getStencil
    from lbmpy.collisionoperator import makeSRT
    from lbmpy.lbmgenerator import createLbmEquations
    from pystencils.backends.cbackend import generateCUDA

    latticeModel = makeSRT(getStencil("D2Q9"), order=2, compressible=False)
    r = createLbmEquations(latticeModel, doCSE=True)
    kernel = createCUDAKernel(r)

    import pycuda.driver as cuda
    import pycuda.autoinit
    from pycuda.compiler import SourceModule
    print(generateCUDA(kernel))

    mod = SourceModule(str(generateCUDA(kernel)))
    func = mod.get_function("kernel")
