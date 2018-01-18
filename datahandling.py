import numpy as np
from abc import ABC, abstractmethod

from lbmpy.stencils import getStencil
from pystencils import Field
from pystencils.field import layoutStringToTuple, spatialLayoutStringToTuple, createNumpyArrayWithLayout
from pystencils.parallel.blockiteration import Block, SerialBlock
from pystencils.slicing import normalizeSlice, removeGhostLayers
from pystencils.utils import DotDict

try:
    import pycuda.gpuarray as gpuarray
except ImportError:
    gpuarray = None


class DataHandling(ABC):
    """
    Manages the storage of arrays and maps them to a symbolic field.
    Two versions are available: a simple, pure Python implementation for single node
    simulations :py:class:SerialDataHandling and a distributed version using waLBerla in :py:class:ParallelDataHandling

    Keep in mind that the data can be distributed, so use the 'access' method whenever possible and avoid the
    'gather' function that has collects (parts of the) distributed data on a single process.
    """

    # ---------------------------- Adding and accessing data -----------------------------------------------------------

    @property
    @abstractmethod
    def dim(self):
        """Dimension of the domain, either 2 or 3"""

    @abstractmethod
    def addArray(self, name, fSize=1, dtype=np.float64, latexName=None, ghostLayers=None, layout=None, cpu=True, gpu=False):
        """
        Adds a (possibly distributed) array to the handling that can be accessed using the given name.
        For each array a symbolic field is available via the 'fields' dictionary

        :param name: unique name that is used to access the field later
        :param fSize: shape of the dim+1 coordinate. DataHandling supports zero or one index dimensions, i.e. scalar
                      fields and vector fields. This parameter gives the shape of the index dimensions. The default
                      value of 1 means no index dimension
        :param dtype: data type of the array as numpy data type
        :param latexName: optional, name of the symbolic field, if not given 'name' is used
        :param ghostLayers: number of ghost layers - if not specified a default value specified in the constructor
                            is used
        :param layout: memory layout of array, either structure of arrays 'SoA' or array of structures 'AoS'.
                       this is only important if fSize > 1
        :param cpu: allocate field on the CPU
        :param gpu: allocate field on the GPU
        """

    @abstractmethod
    def hasData(self, name):
        """
        Returns true if a field or custom data element with this name was added
        """

    @abstractmethod
    def addArrayLike(self, name, nameOfTemplateField, latexName=None, cpu=True, gpu=False):
        """
        Adds an array with the same parameters (number of ghost layers, fSize, dtype) as existing array
        :param name: name of new array
        :param nameOfTemplateField: name of array that is used as template
        :param latexName: see 'add' method
        :param cpu: see 'add' method
        :param gpu: see 'add' method
        """

    @abstractmethod
    def addCustomData(self, name, cpuCreationFunction,
                      gpuCreationFunction=None, cpuToGpuTransferFunc=None, gpuToCpuTransferFunc=None):
        """
        Adds custom (non-array) data to domain
        :param name: name to access data
        :param cpuCreationFunction: function returning a new instance of the data that should be stored
        :param gpuCreationFunction: optional, function returning a new instance, stored on GPU
        :param cpuToGpuTransferFunc: function that transfers cpu to gpu version, getting two parameters (gpuInstance, cpuInstance)
        :param gpuToCpuTransferFunc: function that transfers gpu to cpu version, getting two parameters (gpuInstance, cpuInstance)
        :return:
        """

    def addCustomClass(self, name, classObj, cpu=True, gpu=False):
        self.addCustomData(name,
                           cpuCreationFunction=classObj if cpu else None,
                           gpuCreationFunction=classObj if gpu else None,
                           cpuToGpuTransferFunc=classObj.toGpu if cpu and gpu and hasattr(classObj, 'toGpu') else None,
                           gpuToCpuTransferFunc=classObj.toCpu if cpu and gpu and hasattr(classObj, 'toCpu') else None)

    @property
    @abstractmethod
    def fields(self):
        """Dictionary mapping data name to symbolic pystencils field - use this to create pystencils kernels"""

    @abstractmethod
    def ghostLayersOfField(self, name):
        """Returns the number of ghost layers for a specific field/array"""

    @abstractmethod
    def iterate(self, sliceObj=None, gpu=False, ghostLayers=None):
        """
        Iterate over local part of potentially distributed data structure.
        """

    @abstractmethod
    def gatherArray(self, name, sliceObj=None, allGather=False):
        """
        Gathers part of the domain on a local process. Whenever possible use 'access' instead, since this method copies
        the distributed data to a single process which is inefficient and may exhaust the available memory

        :param name: name of the array to gather
        :param sliceObj: slice expression of the rectangular sub-part that should be gathered
        :param allGather: if False only the root process receives the result, if True all processes
        :return: generator expression yielding the gathered field, the gathered field does not include any ghost layers
        """

    @abstractmethod
    def runKernel(self, kernelFunc, *args, **kwargs):
        """
        Runs a compiled pystencils kernel using the arrays stored in the DataHandling class for all array parameters
        Additional passed arguments are directly passed to the kernel function and override possible parameters from
        the DataHandling
        """

    # ------------------------------- CPU/GPU transfer -----------------------------------------------------------------

    @abstractmethod
    def toCpu(self, name):
        """Copies GPU data of array with specified name to CPU.
        Works only if 'cpu=True' and 'gpu=True' has been used in 'add' method"""
        pass

    @abstractmethod
    def toGpu(self, name):
        """Copies GPU data of array with specified name to GPU.
        Works only if 'cpu=True' and 'gpu=True' has been used in 'add' method"""
        pass

    @abstractmethod
    def allToCpu(self, name):
        """Copies data from GPU to CPU for all arrays that have a CPU and a GPU representation"""
        pass

    @abstractmethod
    def allToGpu(self, name):
        """Copies data from CPU to GPU for all arrays that have a CPU and a GPU representation"""
        pass

    # ------------------------------- Communication --------------------------------------------------------------------

    def synchronizationFunctionCPU(self, names, stencil=None, **kwargs):
        """
        Synchronizes ghost layers for distributed arrays - for serial scenario this has to be called
        for correct periodicity handling
        :param names: what data to synchronize: name of array or sequence of names
        :param stencil: stencil as string defining which neighbors are synchronized e.g. 'D2Q9', 'D3Q19'
                        if None, a full synchronization (i.e. D2Q9 or D3Q27) is done
        :param kwargs: implementation specific, optional optimization parameters for communication
        :return: function object to run the communication
        """

    def synchronizationFunctionGPU(self, names, stencil=None, **kwargs):
        """
        Synchronization of GPU fields, for documentation see CPU version above
        """


class SerialDataHandling(DataHandling):

    class _PassThroughContextManager:
        def __init__(self, arr):
            self.arr = arr

        def __enter__(self, *args, **kwargs):
            return self.arr

    def __init__(self, domainSize, defaultGhostLayers=1, defaultLayout='SoA', periodicity=False):
        """
        Creates a data handling for single node simulations

        :param domainSize: size of the spatial domain as tuple
        :param defaultGhostLayers: nr of ghost layers used if not specified in add() method
        :param defaultLayout: layout used if no layout is given to add() method
        """
        super(SerialDataHandling, self).__init__()
        self._domainSize = tuple(domainSize)
        self.defaultGhostLayers = defaultGhostLayers
        self.defaultLayout = defaultLayout
        self._fields = DotDict()
        self._fieldLatexNameToDataName = {}
        self.cpuArrays = DotDict()
        self.gpuArrays = DotDict()
        self.customDataCpu = DotDict()
        self.customDataGpu = DotDict()
        self._customDataTransferFunctions = {}

        if periodicity is None or periodicity is False:
            periodicity = [False] * self.dim
        if periodicity is True:
            periodicity = [True] * self.dim

        self._periodicity = periodicity
        self._fieldInformation = {}

    @property
    def dim(self):
        return len(self._domainSize)

    @property
    def fields(self):
        return self._fields

    def ghostLayersOfField(self, name):
        return self._fieldInformation[name]['ghostLayers']

    def addArray(self, name, fSize=1, dtype=np.float64, latexName=None, ghostLayers=None, layout=None,
                 cpu=True, gpu=False):
        if ghostLayers is None:
            ghostLayers = self.defaultGhostLayers
        if layout is None:
            layout = self.defaultLayout
        if latexName is None:
            latexName = name

        kwargs = {
            'shape': tuple(s + 2 * ghostLayers for s in self._domainSize),
            'dtype': dtype,
        }
        self._fieldInformation[name] = {
            'ghostLayers': ghostLayers,
            'fSize': fSize,
            'layout': layout,
            'dtype': dtype,
        }

        if fSize > 1:
            kwargs['shape'] = kwargs['shape'] + (fSize,)
            indexDimensions = 1
            layoutTuple = layoutStringToTuple(layout, self.dim+1)
        else:
            indexDimensions = 0
            layoutTuple = spatialLayoutStringToTuple(layout, self.dim)

        # cpuArr is always created - since there is no createPycudaArrayWithLayout()
        cpuArr = createNumpyArrayWithLayout(layout=layoutTuple, **kwargs)
        if cpu:
            if name in self.cpuArrays:
                raise ValueError("CPU Field with this name already exists")
            self.cpuArrays[name] = cpuArr
        if gpu:
            if name in self.gpuArrays:
                raise ValueError("GPU Field with this name already exists")
            self.gpuArrays[name] = gpuarray.to_gpu(cpuArr)

        assert all(f.name != latexName for f in self.fields.values()), "Symbolic field with this name already exists"
        self.fields[name] = Field.createFixedSize(latexName, shape=kwargs['shape'], indexDimensions=indexDimensions,
                                                  dtype=kwargs['dtype'], layout=layout)
        self._fieldLatexNameToDataName[latexName] = name

    def addCustomData(self, name, cpuCreationFunction,
                      gpuCreationFunction=None, cpuToGpuTransferFunc=None, gpuToCpuTransferFunc=None):

        if cpuCreationFunction and gpuCreationFunction:
            if cpuToGpuTransferFunc is None or gpuToCpuTransferFunc is None:
                raise ValueError("For GPU data, both transfer functions have to be specified")
            self._customDataTransferFunctions[name] = (cpuToGpuTransferFunc, gpuToCpuTransferFunc)

        assert name not in self.customDataCpu
        if cpuCreationFunction:
            assert name not in self.cpuArrays
            self.customDataCpu[name] = cpuCreationFunction()

        if gpuCreationFunction:
            assert name not in self.gpuArrays
            self.customDataGpu[name] = gpuCreationFunction()

    def hasData(self, name):
        return name in self.fields

    def addArrayLike(self, name, nameOfTemplateField, latexName=None, cpu=True, gpu=False):
        self.addArray(name, latexName=latexName, cpu=cpu, gpu=gpu, **self._fieldInformation[nameOfTemplateField])

    def iterate(self, sliceObj=None, gpu=False, ghostLayers=True):
        if ghostLayers is True:
            ghostLayers = self.defaultGhostLayers
        elif ghostLayers is False:
            ghostLayers = 0

        if sliceObj is None:
            sliceObj = (slice(None, None, None),) * self.dim
        sliceObj = normalizeSlice(sliceObj, tuple(s + 2 * ghostLayers for s in self._domainSize))
        sliceObj = tuple(s if type(s) is slice else slice(s, s+1, None) for s in sliceObj)

        arrays = self.gpuArrays if gpu else self.cpuArrays
        customDataDict = self.customDataGpu if gpu else self.customDataCpu
        iterDict = customDataDict.copy()
        for name, arr in arrays.items():
            fieldGls = self._fieldInformation[name]['ghostLayers']
            if fieldGls < ghostLayers:
                continue
            arr = removeGhostLayers(arr, indexDimensions=len(arr.shape) - self.dim, ghostLayers=fieldGls-ghostLayers)
            iterDict[name] = arr

        offset = tuple(s.start - ghostLayers for s in sliceObj)
        yield SerialBlock(iterDict, offset, sliceObj)

    def gatherArray(self, name, sliceObj=None, **kwargs):
        gls = self._fieldInformation[name]['ghostLayers']
        arr = self.cpuArrays[name]
        arr = removeGhostLayers(arr, indexDimensions=self.fields[name].indexDimensions, ghostLayers=gls)

        if sliceObj is not None:
            arr = arr[sliceObj]
        yield arr

    def swap(self, name1, name2, gpu=False):
        if not gpu:
            self.cpuArrays[name1], self.cpuArrays[name2] = self.cpuArrays[name2], self.cpuArrays[name1]
        else:
            self.gpuArrays[name1], self.gpuArrays[name2] = self.gpuArrays[name2], self.gpuArrays[name1]

    def allToCpu(self):
        for name in (self.cpuArrays.keys() & self.gpuArrays.keys()) | self._customDataTransferFunctions.keys():
            self.toCpu(name)

    def allToGpu(self):
        for name in (self.cpuArrays.keys() & self.gpuArrays.keys()) | self._customDataTransferFunctions.keys():
            self.toGpu(name)

    def runKernel(self, kernelFunc, *args, **kwargs):
        dataUsedInKernel = [self._fieldLatexNameToDataName[p.fieldName]
                            for p in kernelFunc.parameters if p.isFieldPtrArgument]
        arrays = self.gpuArrays if kernelFunc.ast.backend == 'gpucuda' else self.cpuArrays
        arrayParams = {name: arrays[name] for name in dataUsedInKernel}
        arrayParams.update(kwargs)
        kernelFunc(*args, **arrayParams)

    def toCpu(self, name):
        if name in self._customDataTransferFunctions:
            transferFunc = self._customDataTransferFunctions[name][1]
            transferFunc(self.customDataGpu[name], self.customDataCpu[name])
        else:
            self.gpuArrays[name].get(self.cpuArrays[name])

    def toGpu(self, name):
        if name in self._customDataTransferFunctions:
            transferFunc = self._customDataTransferFunctions[name][0]
            transferFunc(self.customDataGpu[name], self.customDataCpu[name])
        else:
            self.gpuArrays[name].set(self.cpuArrays[name])

    def synchronizationFunctionCPU(self, names, stencilName=None, **kwargs):
        return self._synchronizationFunctor(names, stencilName, 'cpu')

    def synchronizationFunctionGPU(self, names, stencilName=None, **kwargs):
        return self._synchronizationFunctor(names, stencilName, 'gpu')

    def _synchronizationFunctor(self, names, stencil, target):
        if stencil is None:
            stencil = 'D3Q27' if self.dim == 3 else 'D2Q9'

        assert stencil in ("D2Q9", 'D3Q27'), "Serial scenario support only D2Q9 or D3Q27 for periodicity sync"

        assert target in ('cpu', 'gpu')
        if not hasattr(names, '__len__') or type(names) is str:
            names = [names]

        filteredStencil = []
        for direction in getStencil(stencil):
            useDirection = True
            if direction == (0, 0) or direction == (0, 0, 0):
                useDirection = False
            for component, periodicity in zip(direction, self._periodicity):
                if not periodicity and component != 0:
                    useDirection = False
            if useDirection:
                filteredStencil.append(direction)

        resultFunctors = []
        for name in names:
            gls = self._fieldInformation[name]['ghostLayers']
            if len(filteredStencil) > 0:
                if target == 'cpu':
                    from pystencils.slicing import getPeriodicBoundaryFunctor
                    resultFunctors.append(getPeriodicBoundaryFunctor(filteredStencil, ghostLayers=gls))
                else:
                    from pystencils.gpucuda.periodicity import getPeriodicBoundaryFunctor
                    resultFunctors.append(getPeriodicBoundaryFunctor(filteredStencil, self._domainSize,
                                                                     indexDimensions=self.fields[name].indexDimensions,
                                                                     indexDimShape=self._fieldInformation[name]['fSize'],
                                                                     dtype=self.fields[name].dtype.numpyDtype,
                                                                     ghostLayers=gls))

        if target == 'cpu':
            def resultFunctor():
                for func in resultFunctors:
                    func(pdfs=self.cpuArrays[name])
        else:
            def resultFunctor():
                for func in resultFunctors:
                    func(pdfs=self.gpuArrays[name])

        return resultFunctor
