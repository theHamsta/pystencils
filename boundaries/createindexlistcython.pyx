# Workaround for cython bug
# see https://stackoverflow.com/questions/8024805/cython-compiled-c-extension-importerror-dynamic-module-does-not-define-init-fu
WORKAROUND = "Something"

import cython

ctypedef fused IntegerType:
    short
    int
    long
    long long
    unsigned short
    unsigned int
    unsigned long

@cython.boundscheck(False) # turn off bounds-checking for entire function
@cython.wraparound(False)  # turn off negative index wrapping for entire function
def create_boundary_index_list_2d(object[IntegerType, ndim=2] flag_field,
                                  int nr_of_ghost_layers, IntegerType boundary_mask, IntegerType fluid_mask,
                                  object[int, ndim=2] stencil):
    cdef int xs, ys, x, y
    cdef int dirIdx, num_directions, dx, dy

    xs, ys = flag_field.shape
    boundary_index_list = []
    num_directions = stencil.shape[0]

    for y in range(nr_of_ghost_layers, ys - nr_of_ghost_layers):
        for x in range(nr_of_ghost_layers, xs - nr_of_ghost_layers):
            if flag_field[x, y] & fluid_mask:
                for dirIdx in range(1, num_directions):
                    dx = stencil[dirIdx,0]
                    dy = stencil[dirIdx,1]
                    if flag_field[x + dx, y + dy] & boundary_mask:
                        boundary_index_list.append((x,y, dirIdx))
    return boundary_index_list


@cython.boundscheck(False) # turn off bounds-checking for entire function
@cython.wraparound(False)  # turn off negative index wrapping for entire function
def create_boundary_index_list_3d(object[IntegerType, ndim=3] flagField,
                                  int nrOfGhostLayers, IntegerType boundaryMask, IntegerType fluidMask,
                                  object[int, ndim=2] stencil):
    cdef int xs, ys, zs, x, y, z
    cdef int dirIdx, num_directions, dx, dy, dz

    xs, ys, zs = flagField.shape
    boundary_index_list = []
    num_directions = stencil.shape[0]

    for z in range(nrOfGhostLayers, zs-nrOfGhostLayers):
        for y in range(nrOfGhostLayers,ys-nrOfGhostLayers):
            for x in range(nrOfGhostLayers,xs-nrOfGhostLayers):
                if flagField[x, y, z] & fluidMask:
                    for dirIdx in range(1, num_directions):
                        dx = stencil[dirIdx,0]
                        dy = stencil[dirIdx,1]
                        dz = stencil[dirIdx,2]
                        if flagField[x + dx, y + dy, z + dz] & boundaryMask:
                            boundary_index_list.append((x,y,z, dirIdx))
    return boundary_index_list


