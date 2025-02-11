import os
from collections import Counter
from contextlib import contextmanager
from tempfile import NamedTemporaryFile
from typing import Mapping

import numpy as np
import sympy as sp


class DotDict(dict):
    """Normal dict with additional dot access for all keys"""
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__


def all_equal(iterator):
    iterator = iter(iterator)
    try:
        first = next(iterator)
    except StopIteration:
        return True
    return all(first == rest for rest in iterator)


def recursive_dict_update(d, u):
    """Updates the first dict argument, using second dictionary recursively.

    Examples:
        >>> d = {'sub_dict': {'a': 1, 'b': 2}, 'outer': 42}
        >>> u = {'sub_dict': {'a': 5, 'c': 10}, 'outer': 41, 'outer2': 43}
        >>> recursive_dict_update(d, u)
        {'sub_dict': {'a': 5, 'b': 2, 'c': 10}, 'outer': 41, 'outer2': 43}
    """
    d = d.copy()
    for k, v in u.items():
        if isinstance(v, Mapping):
            r = recursive_dict_update(d.get(k, {}), v)
            d[k] = r
        else:
            d[k] = u[k]
    return d


@contextmanager
def file_handle_for_atomic_write(file_path):
    """Open temporary file object that atomically moves to destination upon exiting.

    Allows reading and writing to and from the same filename.
    The file will not be moved to destination in case of an exception.

    Args:
        file_path: path to file to be opened
    """
    target_folder = os.path.dirname(os.path.abspath(file_path))
    with NamedTemporaryFile(delete=False, dir=target_folder, mode='w') as f:
        try:
            yield f
        finally:
            f.flush()
            os.fsync(f.fileno())
    os.rename(f.name, file_path)


@contextmanager
def atomic_file_write(file_path):
    target_folder = os.path.dirname(os.path.abspath(file_path))
    with NamedTemporaryFile(delete=False, dir=target_folder) as f:
        f.file.close()
        yield f.name
    os.rename(f.name, file_path)


def fully_contains(l1, l2):
    """Tests if elements of sequence 1 are in sequence 2 in same or higher number.

    >>> fully_contains([1, 1, 2], [1, 2])  # 1 is only present once in second list
    False
    >>> fully_contains([1, 1, 2], [1, 1, 4, 2])
    True
    """
    l1_counter = Counter(l1)
    l2_counter = Counter(l2)
    for element, count in l1_counter.items():
        if l2_counter[element] < count:
            return False
    return True


def boolean_array_bounding_box(boolean_array):
    """Returns bounding box around "true" area of boolean array"""
    dim = len(boolean_array.shape)
    bounds = []
    for i in range(dim):
        for j in range(dim):
            if i != j:
                arr_1d = np.any(boolean_array, axis=j)
        begin = np.argmax(arr_1d)
        end = begin + np.argmin(arr_1d[begin:])
        bounds.append((begin, end))
    return bounds


class LinearEquationSystem:
    """Symbolic linear system of equations - consisting of matrix and right hand side.

    Equations can be added incrementally. System is held in reduced row echelon form to quickly determine if
    system has a single, multiple, or no solution.

    Example:
        >>> x, y= sp.symbols("x, y")
        >>> les = LinearEquationSystem([x, y])
        >>> les.add_equation(x - y - 3)
        >>> les.solution_structure()
        'multiple'
        >>> les.add_equation(x + y - 4)
        >>> les.solution_structure()
        'single'
        >>> les.solution()
        {x: 7/2, y: 1/2}

    """
    def __init__(self, unknowns):
        size = len(unknowns)
        self._matrix = sp.zeros(size, size + 1)
        self.unknowns = unknowns
        self.next_zero_row = 0
        self._reduced = True

    def copy(self):
        """Returns a copy of the equation system."""
        new = LinearEquationSystem(self.unknowns)
        new._matrix = self._matrix.copy()
        new.next_zero_row = self.next_zero_row
        return new

    def add_equation(self, linear_equation):
        """Add a linear equation as sympy expression. Implicit "-0" is assumed. Equation has to be linear and contain
        only unknowns passed to the constructor otherwise a ValueError is raised. """
        self._resize_if_necessary()
        linear_equation = linear_equation.expand()
        zero_row_idx = self.next_zero_row
        self.next_zero_row += 1

        control = 0
        for i, unknown in enumerate(self.unknowns):
            self._matrix[zero_row_idx, i] = linear_equation.coeff(unknown)
            control += unknown * self._matrix[zero_row_idx, i]
        rest = linear_equation - control
        if rest.atoms(sp.Symbol):
            raise ValueError("Not a linear equation in the unknowns")
        self._matrix[zero_row_idx, -1] = -rest
        self._reduced = False

    def add_equations(self, linear_equations):
        """Add a sequence of equations. For details see `add_equation`. """
        self._resize_if_necessary(len(linear_equations))
        for eq in linear_equations:
            self.add_equation(eq)

    def set_unknown_zero(self, unknown_idx):
        """Sets an unknown to zero - pass the index not the variable itself!"""
        assert unknown_idx < len(self.unknowns)
        self._resize_if_necessary()
        self._matrix[self.next_zero_row, unknown_idx] = 1
        self.next_zero_row += 1
        self._reduced = False

    def reduce(self):
        """Brings the system in reduced row echelon form."""
        if self._reduced:
            return
        self._matrix = self._matrix.rref()[0]
        self._update_next_zero_row()
        self._reduced = True

    @property
    def matrix(self):
        """Return a matrix that represents the equation system.
        Has one column more than unknowns for the affine part."""
        self.reduce()
        return self._matrix

    @property
    def rank(self):
        self.reduce()
        return self.next_zero_row

    def solution_structure(self):
        """Returns either 'multiple', 'none' or 'single' to indicate how many solutions the system has."""
        self.reduce()
        non_zero_rows = self.next_zero_row
        num_unknowns = len(self.unknowns)
        if non_zero_rows == 0:
            return 'multiple'

        *row_begin, left, right = self._matrix.row(non_zero_rows - 1)
        if non_zero_rows > num_unknowns:
            return 'none'
        elif non_zero_rows == num_unknowns:
            if left == 0 and right != 0:
                return 'none'
            else:
                return 'single'
        elif non_zero_rows < num_unknowns:
            if right != 0 and left == 0 and all(e == 0 for e in row_begin):
                return 'none'
            else:
                return 'multiple'

    def solution(self):
        """Solves the system if it has a single solution. Returns a dictionary mapping symbol to solution value."""
        return sp.solve_linear_system(self._matrix, *self.unknowns)

    def _resize_if_necessary(self, new_rows=1):
        if self.next_zero_row + new_rows > self._matrix.shape[0]:
            self._matrix = self._matrix.row_insert(self._matrix.shape[0] + 1,
                                                   sp.zeros(new_rows, self._matrix.shape[1]))

    def _update_next_zero_row(self):
        result = self._matrix.shape[0]
        while result >= 0:
            row_to_check = result - 1
            if any(e != 0 for e in self._matrix.row(row_to_check)):
                break
            result -= 1
        self.next_zero_row = result


def find_unique_solutions_with_zeros(system: LinearEquationSystem):
    if not system.solution_structure() != 'multiple':
        raise ValueError("Function works only for underdetermined systems")
