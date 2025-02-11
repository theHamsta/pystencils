import os
import pytest
import tempfile
import runpy
import sys
# Trigger config file reading / creation once - to avoid race conditions when multiple instances are creating it
# at the same time
from pystencils.cpu import cpujit

# trigger cython imports - there seems to be a problem when multiple processes try to compile the same cython file
# at the same time
try:
    import pyximport
    pyximport.install(language_level=3)
except ImportError:
    pass
from pystencils.boundaries.createindexlistcython import *  # NOQA


SCRIPT_FOLDER = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.abspath('pystencils'))


def add_path_to_ignore(path):
    if not os.path.exists(path):
        return
    global collect_ignore
    collect_ignore += [os.path.join(SCRIPT_FOLDER, path, f) for f in os.listdir(os.path.join(SCRIPT_FOLDER, path))]


collect_ignore = [os.path.join(SCRIPT_FOLDER, "doc", "conf.py")]
add_path_to_ignore('pystencils_tests/benchmark')
add_path_to_ignore('_local_tmp')


collect_ignore += [os.path.join(SCRIPT_FOLDER, "pystencils/autodiff.py")]

try:
    import pycuda
except ImportError:
    collect_ignore += [os.path.join(SCRIPT_FOLDER, "pystencils/pystencils_tests/test_cudagpu.py")]
    add_path_to_ignore('pystencils/gpucuda')

try:
    import llvmlite
except ImportError:
    collect_ignore += [os.path.join(SCRIPT_FOLDER, 'pystencils_tests/backends/llvm.py')]
    add_path_to_ignore('pystencils/llvm')

try:
    import kerncraft
except ImportError:
    collect_ignore += [os.path.join(SCRIPT_FOLDER, "pystencils_tests/test_kerncraft_coupling.py"),
                       os.path.join(SCRIPT_FOLDER, "pystencils_tests/benchmark/benchmark.py")]
    add_path_to_ignore('pystencils/kerncraft_coupling')

try:
    import waLBerla
except ImportError:
    collect_ignore += [os.path.join(SCRIPT_FOLDER, "pystencils_tests/test_aligned_array.py"),
                       os.path.join(SCRIPT_FOLDER, "pystencils_tests/test_datahandling_parallel.py"),
                       os.path.join(SCRIPT_FOLDER, "doc/notebooks/03_tutorial_datahandling.ipynb"),
                       os.path.join(SCRIPT_FOLDER, "pystencils/datahandling/parallel_datahandling.py"),
                       os.path.join(SCRIPT_FOLDER, "pystencils_tests/test_small_block_benchmark.ipynb")]

try:
    import blitzdb
except ImportError:
    add_path_to_ignore('pystencils/runhelper')


collect_ignore += [os.path.join(SCRIPT_FOLDER, 'setup.py')]

for root, sub_dirs, files in os.walk('.'):
    for f in files:
        if f.endswith(".ipynb") and not any(f.startswith(k) for k in ['demo', 'tutorial', 'test', 'doc']):
            collect_ignore.append(f)


import nbformat
from nbconvert import PythonExporter


class IPythonMockup:
    def run_line_magic(self, *args, **kwargs):
        pass

    def run_cell_magic(self, *args, **kwargs):
        pass

    def magic(self, *args, **kwargs):
        pass

    def __bool__(self):
        return False


class IPyNbTest(pytest.Item):
    def __init__(self, name, parent, code):
        super(IPyNbTest, self).__init__(name, parent)
        self.code = code
        self.add_marker('notebook')

    @pytest.mark.filterwarnings("ignore:IPython.core.inputsplitter is deprecated")
    def runtest(self):
        global_dict = {'get_ipython': lambda: IPythonMockup(),
                       'is_test_run': True}

        # disable matplotlib output
        exec("import matplotlib.pyplot as p; "
             "p.switch_backend('Template')", global_dict)

        # in notebooks there is an implicit plt.show() - if this is not called a warning is shown when the next
        # plot is created. This warning is suppressed here
        exec("import warnings;"
             "warnings.filterwarnings('ignore', 'Adding an axes using the same arguments as a previous.*')",
             global_dict)
        with tempfile.NamedTemporaryFile() as f:
            f.write(self.code.encode())
            f.flush()
            runpy.run_path(f.name, init_globals=global_dict, run_name=self.name)


class IPyNbFile(pytest.File):
    def collect(self):
        exporter = PythonExporter()
        exporter.exclude_markdown = True
        exporter.exclude_input_prompt = True

        notebook_contents = self.fspath.open()
        notebook = nbformat.read(notebook_contents, 4)
        code, _ = exporter.from_notebook_node(notebook)
        yield IPyNbTest(self.name, self, code)

    def teardown(self):
        pass


def pytest_collect_file(path, parent):
    glob_exprs = ["*demo*.ipynb", "*tutorial*.ipynb", "test_*.ipynb"]
    if any(path.fnmatch(g) for g in glob_exprs):
        return IPyNbFile(path, parent)
