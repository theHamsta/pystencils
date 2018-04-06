import sympy as sp
from typing import Callable, List
from pystencils.assignment import Assignment
from pystencils.assignment_collection.assignment_collection import AssignmentCollection
from pystencils.sympyextensions import subs_additive


def sympy_cse(ac: AssignmentCollection) -> AssignmentCollection:
    """Searches for common subexpressions inside the equation collection.

    Searches is done in both the existing subexpressions as well as the assignments themselves.
    It uses the sympy subexpression detection to do this. Return a new equation collection
    with the additional subexpressions found
    """
    symbol_gen = ac.subexpression_symbol_generator
    replacements, new_eq = sp.cse(ac.subexpressions + ac.main_assignments,
                                  symbols=symbol_gen)
    replacement_eqs = [Assignment(*r) for r in replacements]

    modified_subexpressions = new_eq[:len(ac.subexpressions)]
    modified_update_equations = new_eq[len(ac.subexpressions):]

    new_subexpressions = replacement_eqs + modified_subexpressions
    topologically_sorted_pairs = sp.cse_main.reps_toposort([[e.lhs, e.rhs] for e in new_subexpressions])
    new_subexpressions = [Assignment(a[0], a[1]) for a in topologically_sorted_pairs]

    return ac.copy(modified_update_equations, new_subexpressions)


def sympy_cse_on_assignment_list(assignments: List[Assignment]) -> List[Assignment]:
    """Extracts common subexpressions from a list of assignments."""
    ec = AssignmentCollection([], assignments)
    return sympy_cse(ec).all_assignments


def apply_to_all_assignments(assignment_collection: AssignmentCollection,
                             operation: Callable[[sp.Expr], sp.Expr]) -> AssignmentCollection:
    """Applies sympy expand operation to all equations in collection."""
    result = [Assignment(eq.lhs, operation(eq.rhs)) for eq in assignment_collection.main_assignments]
    return assignment_collection.copy(result)


def apply_on_all_subexpressions(ac: AssignmentCollection,
                                operation: Callable[[sp.Expr], sp.Expr]) -> AssignmentCollection:
    """Applies the given operation on all subexpressions of the AssignmentCollection."""
    result = [Assignment(eq.lhs, operation(eq.rhs)) for eq in ac.subexpressions]
    return ac.copy(ac.main_assignments, result)


def subexpression_substitution_in_existing_subexpressions(ac: AssignmentCollection) -> AssignmentCollection:
    """Goes through the subexpressions list and replaces the term in the following subexpressions."""
    result = []
    for outerCtr, s in enumerate(ac.subexpressions):
        new_rhs = s.rhs
        for innerCtr in range(outerCtr):
            sub_expr = ac.subexpressions[innerCtr]
            new_rhs = subs_additive(new_rhs, sub_expr.lhs, sub_expr.rhs, required_match_replacement=1.0)
            new_rhs = new_rhs.subs(sub_expr.rhs, sub_expr.lhs)
        result.append(Assignment(s.lhs, new_rhs))

    return ac.copy(ac.main_assignments, result)


def subexpression_substitution_in_main_assignments(ac: AssignmentCollection) -> AssignmentCollection:
    """Replaces already existing subexpressions in the equations of the assignment_collection."""
    result = []
    for s in ac.main_assignments:
        new_rhs = s.rhs
        for subExpr in ac.subexpressions:
            new_rhs = subs_additive(new_rhs, subExpr.lhs, subExpr.rhs, required_match_replacement=1.0)
        result.append(Assignment(s.lhs, new_rhs))
    return ac.copy(result)


def add_subexpressions_for_divisions(ac: AssignmentCollection) -> AssignmentCollection:
    """Introduces subexpressions for all divisions which have no constant in the denominator.

    For example :math:`\frac{1}{x}` is replaced, :math:`\frac{1}{3}` is not replaced.
    """
    divisors = set()

    def search_divisors(term):
        if term.func == sp.Pow:
            if term.exp.is_integer and term.exp.is_number and term.exp < 0:
                divisors.add(term)
        else:
            for a in term.args:
                search_divisors(a)

    for eq in ac.all_assignments:
        search_divisors(eq.rhs)

    new_symbol_gen = ac.subexpression_symbol_generator
    substitutions = {divisor: newSymbol for newSymbol, divisor in zip(new_symbol_gen, divisors)}
    return ac.new_with_substitutions(substitutions, True)
