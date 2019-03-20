from pystencils import create_data_handling
from pystencils.alignedarray import *
from pystencils.field import create_numpy_array_with_layout


def is_aligned(arr, alignment, byte_offset=0):
    address = arr.__array_interface__['data'][0]
    rest = (address + byte_offset) % alignment
    if rest:
        print("Alignment rest", rest)
    return rest == 0


def test_1d_arrays():
    for alignment in [8, 8*4]:
        for shape in [17, 16, (16, 16), (17, 17), (18, 18), (19, 19)]:
            arrays = [
                aligned_zeros(shape, alignment),
                aligned_ones(shape, alignment),
                aligned_empty(shape, alignment),
            ]
            for arr in arrays:
                assert is_aligned(arr, alignment)


def test_3d_arrays():
    for order in ('C', 'F'):
        for alignment in [8, 8*4]:
            for shape in [(16, 16), (17, 17), (18, 18), (19, 19)]:
                arrays = [
                    aligned_zeros(shape, alignment, order=order),
                    aligned_ones(shape, alignment, order=order),
                    aligned_empty(shape, alignment, order=order),
                ]
                for arr in arrays:
                    assert is_aligned(arr, alignment)
                    if order == 'C':
                        assert is_aligned(arr[1], alignment)
                        assert is_aligned(arr[5], alignment)
                    else:
                        assert is_aligned(arr[..., 1], alignment)
                        assert is_aligned(arr[..., 5], alignment)


def test_data_handling():
    for parallel in (False, True):
        for tries in range(16):  # try a few times, since we might get lucky and get randomly a correct alignment
            dh = create_data_handling((6, 7), default_ghost_layers=1, parallel=parallel)
            dh.add_array('test', alignment=8 * 4)
            for b in dh.iterate(ghost_layers=True, inner_ghost_layers=True):
                arr = b['test']
                assert is_aligned(arr[1:, 3:], 8*4)


def test_alignment_of_different_layouts():
    offset = 1
    byte_offset = 8
    for tries in range(16):  # try a few times, since we might get lucky and get randomly a correct alignment
        arr = create_numpy_array_with_layout((3, 4, 5), layout=(0, 1, 2),
                                             alignment=True, byte_offset=byte_offset)
        assert is_aligned(arr[offset, ...], 8*4, byte_offset)

        arr = create_numpy_array_with_layout((3, 4, 5), layout=(2, 1, 0),
                                             alignment=True, byte_offset=byte_offset)
        assert is_aligned(arr[..., offset], 8*4, byte_offset)

        arr = create_numpy_array_with_layout((3, 4, 5), layout=(2, 0, 1),
                                             alignment=True, byte_offset=byte_offset)
        assert is_aligned(arr[:, 0, :], 8*4, byte_offset)
