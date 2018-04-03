from sympy.printing.printer import Printer
from graphviz import Digraph, lang
import graphviz


# noinspection PyPep8Naming
class DotPrinter(Printer):
    """
    A printer which converts ast to DOT (graph description language).
    """
    def __init__(self, node_to_str_function, full, **kwargs):
        super(DotPrinter, self).__init__()
        self._nodeToStrFunction = node_to_str_function
        self.full = full
        self.dot = Digraph(**kwargs)
        self.dot.quote_edge = lang.quote

    def _print_KernelFunction(self, func):
        self.dot.node(str(id(func)), style='filled', fillcolor='#a056db', label=self._nodeToStrFunction(func))
        self._print(func.body)
        self.dot.edge(str(id(func)), str(id(func.body)))

    def _print_LoopOverCoordinate(self, loop):
        self.dot.node(str(id(loop)), style='filled', fillcolor='#3498db', label=self._nodeToStrFunction(loop))
        self._print(loop.body)
        self.dot.edge(str(id(loop)), str(id(loop.body)))

    def _print_Block(self, block):
        for node in block.args:
            self._print(node)

        self.dot.node(str(id(block)), style='filled', fillcolor='#dbc256', label=repr(block))
        for node in block.args:
            self.dot.edge(str(id(block)), str(id(node)))

    def _print_SympyAssignment(self, assignment):
        self.dot.node(str(id(assignment)), style='filled', fillcolor='#56db7f',
                      label=self._nodeToStrFunction(assignment))
        if self.full:
            for node in assignment.args:
                self._print(node)
            for node in assignment.args:
                self.dot.edge(str(id(assignment)), str(id(node)))

    def _print_Conditional(self, expr):
        self.dot.node(str(id(expr)), style='filled', fillcolor='#56bd7f', label=self._nodeToStrFunction(expr))
        self._print(expr.trueBlock)
        self.dot.edge(str(id(expr)), str(id(expr.trueBlock)))
        if expr.falseBlock:
            self._print(expr.falseBlock)
            self.dot.edge(str(id(expr)), str(id(expr.falseBlock)))

    def empty_printer(self, expr):
        if self.full:
            self.dot.node(str(id(expr)), label=self._nodeToStrFunction(expr))
            for node in expr.args:
                self._print(node)
            for node in expr.args:
                self.dot.edge(str(id(expr)), str(id(node)))
        else:
            raise NotImplementedError('DotPrinter cannot print', type(expr), expr)

    def doprint(self, expr):
        self._print(expr)
        return self.dot.source


def __shortened(node):
    from pystencils.astnodes import LoopOverCoordinate, KernelFunction, SympyAssignment, Block, Conditional
    if isinstance(node, LoopOverCoordinate):
        return "Loop over dim %d" % (node.coordinateToLoopOver,)
    elif isinstance(node, KernelFunction):
        params = [f.name for f in node.fields_accessed]
        params += [p.name for p in node.parameters if not p.isFieldArgument]
        return "Func: %s (%s)" % (node.function_name, ",".join(params))
    elif isinstance(node, SympyAssignment):
        return repr(node.lhs)
    elif isinstance(node, Block):
        return "Block" + str(id(node))
    elif isinstance(node, Conditional):
        return repr(node)
    else:
        raise NotImplementedError("Cannot handle node type %s" % (type(node),))


def print_dot(node, view=False, short=False, full=False, **kwargs):
    """
    Returns a string which can be used to generate a DOT-graph
    :param node: The ast which should be generated
    :param view: Boolean, if rendering of the image directly should occur.
    :param short: Uses the __shortened output
    :param full: Prints the whole tree with type information
    :param kwargs: is directly passed to the DotPrinter class: http://graphviz.readthedocs.io/en/latest/api.html#digraph
    :return: string in DOT format
    """
    node_to_str_function = repr
    if short:
        node_to_str_function = __shortened
    elif full:
        def node_to_str_function(expr):
            return repr(type(expr)) + repr(expr)
    printer = DotPrinter(node_to_str_function, full, **kwargs)
    dot = printer.doprint(node)
    if view:
        return graphviz.Source(dot)
    return dot

