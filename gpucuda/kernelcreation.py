from functools import partial

from pystencils.gpucuda.indexing import BlockIndexing
from pystencils.transformations import resolve_field_accesses, type_all_equations, parse_base_pointer_info, get_common_shape, \
    substitute_array_accesses_with_constants, resolve_buffer_accesses
from pystencils.astnodes import Block, KernelFunction, SympyAssignment, LoopOverCoordinate
from pystencils.data_types import TypedSymbol, BasicType, StructType
from pystencils import Field, FieldType
from pystencils.gpucuda.cudajit import make_python_function


def create_cuda_kernel(assignments, function_name="kernel", type_info=None, indexing_creator=BlockIndexing,
                       iteration_slice=None, ghost_layers=None):
    fields_read, fields_written, assignments = type_all_equations(assignments, type_info)
    all_fields = fields_read.union(fields_written)
    read_only_fields = set([f.name for f in fields_read - fields_written])

    buffers = set([f for f in all_fields if FieldType.is_buffer(f)])
    fields_without_buffers = all_fields - buffers

    field_accesses = set()
    num_buffer_accesses = 0
    for eq in assignments:
        field_accesses.update(eq.atoms(Field.Access))

        num_buffer_accesses += sum([1 for access in eq.atoms(Field.Access) if FieldType.is_buffer(access.field)])

    common_shape = get_common_shape(fields_without_buffers)

    if iteration_slice is None:
        # determine iteration slice from ghost layers
        if ghost_layers is None:
            # determine required number of ghost layers from field access
            required_ghost_layers = max([fa.required_ghost_layers for fa in field_accesses])
            ghost_layers = [(required_ghost_layers, required_ghost_layers)] * len(common_shape)
        iteration_slice = []
        if isinstance(ghost_layers, int):
            for i in range(len(common_shape)):
                iteration_slice.append(slice(ghost_layers, -ghost_layers if ghost_layers > 0 else None))
        else:
            for i in range(len(common_shape)):
                iteration_slice.append(slice(ghost_layers[i][0], -ghost_layers[i][1] if ghost_layers[i][1] > 0 else None))

    indexing = indexing_creator(field=list(fields_without_buffers)[0], iteration_slice=iteration_slice)

    block = Block(assignments)
    block = indexing.guard(block, common_shape)
    ast = KernelFunction(block, function_name=function_name, ghost_layers=ghost_layers, backend='gpucuda')
    ast.global_variables.update(indexing.index_variables)

    coord_mapping = indexing.coordinates
    base_pointer_info = [['spatialInner0']]
    base_pointer_infos = {f.name: parse_base_pointer_info(base_pointer_info, [2, 1, 0], f) for f in all_fields}

    coord_mapping = {f.name: coord_mapping for f in all_fields}

    loop_vars = [num_buffer_accesses * i for i in indexing.coordinates]
    loop_strides = list(fields_without_buffers)[0].shape

    base_buffer_index = loop_vars[0]
    stride = 1
    for idx, var in enumerate(loop_vars[1:]):
        stride *= loop_strides[idx]
        base_buffer_index += var * stride

    resolve_buffer_accesses(ast, base_buffer_index, read_only_fields)
    resolve_field_accesses(ast, read_only_fields, field_to_base_pointer_info=base_pointer_infos,
                           field_to_fixed_coordinates=coord_mapping)

    substitute_array_accesses_with_constants(ast)

    # add the function which determines #blocks and #threads as additional member to KernelFunction node
    # this is used by the jit

    # If loop counter symbols have been explicitly used in the update equations (e.g. for built in periodicity),
    # they are defined here
    undefined_loop_counters = {LoopOverCoordinate.is_loop_counter_symbol(s): s for s in ast.body.undefined_symbols
                               if LoopOverCoordinate.is_loop_counter_symbol(s) is not None}
    for i, loop_counter in undefined_loop_counters.items():
        ast.body.insert_front(SympyAssignment(loop_counter, indexing.coordinates[i]))

    ast.indexing = indexing
    ast.compile = partial(make_python_function, ast)
    return ast


def created_indexed_cuda_kernel(assignments, index_fields, function_name="kernel", type_info=None,
                                coordinate_names=('x', 'y', 'z'), indexing_creator=BlockIndexing):
    fields_read, fields_written, assignments = type_all_equations(assignments, type_info)
    all_fields = fields_read.union(fields_written)
    read_only_fields = set([f.name for f in fields_read - fields_written])

    for index_field in index_fields:
        index_field.field_type = FieldType.INDEXED
        assert FieldType.is_indexed(index_field)
        assert index_field.spatial_dimensions == 1, "Index fields have to be 1D"

    non_index_fields = [f for f in all_fields if f not in index_fields]
    spatial_coordinates = {f.spatial_dimensions for f in non_index_fields}
    assert len(spatial_coordinates) == 1, "Non-index fields do not have the same number of spatial coordinates"
    spatial_coordinates = list(spatial_coordinates)[0]

    def get_coordinate_symbol_assignment(name):
        for index_field in index_fields:
            assert isinstance(index_field.dtype, StructType), "Index fields have to have a struct data type"
            data_type = index_field.dtype
            if data_type.has_element(name):
                rhs = index_field[0](name)
                lhs = TypedSymbol(name, BasicType(data_type.get_element_type(name)))
                return SympyAssignment(lhs, rhs)
        raise ValueError("Index %s not found in any of the passed index fields" % (name,))

    coordinate_symbol_assignments = [get_coordinate_symbol_assignment(n)
                                     for n in coordinate_names[:spatial_coordinates]]
    coordinate_typed_symbols = [eq.lhs for eq in coordinate_symbol_assignments]

    idx_field = list(index_fields)[0]
    indexing = indexing_creator(field=idx_field,
                                iteration_slice=[slice(None, None, None)] * len(idx_field.spatial_shape))

    function_body = Block(coordinate_symbol_assignments + assignments)
    function_body = indexing.guard(function_body, get_common_shape(index_fields))
    ast = KernelFunction(function_body, function_name=function_name, backend='gpucuda')
    ast.global_variables.update(indexing.index_variables)

    coord_mapping = indexing.coordinates
    base_pointer_info = [['spatialInner0']]
    base_pointer_infos = {f.name: parse_base_pointer_info(base_pointer_info, [2, 1, 0], f) for f in all_fields}

    coord_mapping = {f.name: coord_mapping for f in index_fields}
    coord_mapping.update({f.name: coordinate_typed_symbols for f in non_index_fields})
    resolve_field_accesses(ast, read_only_fields, field_to_fixed_coordinates=coord_mapping,
                           field_to_base_pointer_info=base_pointer_infos)
    substitute_array_accesses_with_constants(ast)

    # add the function which determines #blocks and #threads as additional member to KernelFunction node
    # this is used by the jit
    ast.indexing = indexing
    ast.compile = partial(make_python_function, ast)
    return ast


