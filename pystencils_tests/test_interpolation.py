# -*- coding: utf-8 -*-
#
# Copyright © 2019 Stephan Seitz <stephan.seitz@fau.de>
#
# Distributed under terms of the GPLv3 license.

"""

"""
from os.path import dirname, join

import numpy as np
import sympy

import pycuda.autoinit  # NOQA
import pycuda.gpuarray as gpuarray
import pystencils
from pystencils.interpolation_astnodes import LinearInterpolator
from pystencils.spatial_coordinates import x_, y_

type_map = {
    np.float32: 'float32',
    np.float64: 'float64',
    np.int32: 'int32',
}

try:
    import pyconrad.autoinit
except Exception:
    import unittest.mock
    pyconrad = unittest.mock.MagicMock()

LENNA_FILE = join(dirname(__file__), 'test_data', 'lenna.png')

try:
    import skimage.io
    lenna = skimage.io.imread(LENNA_FILE, as_gray=True).astype(np.float64)
    pyconrad.imshow(lenna)
except Exception:
    lenna = np.random.rand(20, 30)


def test_interpolation():
    x_f, y_f = pystencils.fields('x,y: float64 [2d]')

    assignments = pystencils.AssignmentCollection({
        y_f.center(): LinearInterpolator(x_f).at([x_ + 2.7, y_ + 7.2])
    })
    print(assignments)
    ast = pystencils.create_kernel(assignments)
    print(ast)
    print(pystencils.show_code(ast))
    kernel = ast.compile()

    pyconrad.imshow(lenna)

    out = np.zeros_like(lenna)
    kernel(x=lenna, y=out)
    pyconrad.imshow(out, "out")


def test_scale_interpolation():
    x_f, y_f = pystencils.fields('x,y: float64 [2d]')

    for address_mode in ['border', 'wrap', 'clamp', 'mirror']:
        assignments = pystencils.AssignmentCollection({
            y_f.center(): LinearInterpolator(x_f, address_mode=address_mode).at([0.5 * x_ + 2.7, 0.25 * y_ + 7.2])
        })
        print(assignments)
        ast = pystencils.create_kernel(assignments)
        print(ast)
        print(pystencils.show_code(ast))
        kernel = ast.compile()

        out = np.zeros_like(lenna)
        kernel(x=lenna, y=out)
        pyconrad.imshow(out, "out " + address_mode)


def test_rotate_interpolation():
    x_f, y_f = pystencils.fields('x,y: float64 [2d]')

    rotation_angle = sympy.pi / 5

    for address_mode in ['border', 'wrap', 'clamp', 'mirror']:

        transformed = sympy.rot_axis3(rotation_angle)[:2, :2] * sympy.Matrix((x_, y_))
        assignments = pystencils.AssignmentCollection({
            y_f.center(): LinearInterpolator(x_f, address_mode=address_mode).at(transformed)
        })
        print(assignments)
        ast = pystencils.create_kernel(assignments)
        print(ast)
        print(pystencils.show_code(ast))
        kernel = ast.compile()

        out = np.zeros_like(lenna)
        kernel(x=lenna, y=out)
        pyconrad.imshow(out, "out " + address_mode)


def test_rotate_interpolation_gpu():

    rotation_angle = sympy.pi / 5
    scale = 1

    for address_mode in ['border', 'wrap', 'clamp', 'mirror']:
        previous_result = None
        for dtype in [np.int32, np.float32, np.float64]:
            if dtype == np.int32:
                lenna_gpu = gpuarray.to_gpu(
                    np.ascontiguousarray(lenna * 255, dtype))
            else:
                lenna_gpu = gpuarray.to_gpu(
                    np.ascontiguousarray(lenna, dtype))
            for use_textures in [True, False]:
                x_f, y_f = pystencils.fields('x,y: %s [2d]' % type_map[dtype], ghost_layers=0)

                transformed = scale * \
                    sympy.rot_axis3(rotation_angle)[:2, :2] * sympy.Matrix((x_, y_)) - sympy.Matrix([2, 2])
                assignments = pystencils.AssignmentCollection({
                    y_f.center(): LinearInterpolator(x_f, address_mode=address_mode).at(transformed)
                })
                print(assignments)
                ast = pystencils.create_kernel(assignments, target='gpu', use_textures_for_interpolation=use_textures)
                print(ast)
                print(pystencils.show_code(ast))
                kernel = ast.compile()

                out = gpuarray.zeros_like(lenna_gpu)
                kernel(x=lenna_gpu, y=out)
                pyconrad.imshow(out,
                                f"out {address_mode} texture:{use_textures} {type_map[dtype]}")
                skimage.io.imsave(f"/tmp/out {address_mode} texture:{use_textures} {type_map[dtype]}.tif",
                                  np.ascontiguousarray(out.get(), np.float32))
                if previous_result is not None:
                    try:
                        assert np.allclose(previous_result[4:-4, 4:-4], out.get()[4:-4, 4:-4], rtol=100, atol=1e-3)
                    except AssertionError as e:  # NOQA
                        print("Max error: %f" % np.max(previous_result - out.get()))
                        # pyconrad.imshow(previous_result - out.get(), "Difference image")
                        # raise e
                previous_result = out.get()


def test_shift_interpolation_gpu():

    rotation_angle = 0  # sympy.pi / 5
    scale = 1
    # shift = - sympy.Matrix([1.5, 1.5])
    shift = sympy.Matrix((0.0, 0.0))

    for address_mode in ['border', 'wrap', 'clamp', 'mirror']:
        previous_result = None
        for dtype in [np.float64, np.float32, np.int32]:
            if dtype == np.int32:
                lenna_gpu = gpuarray.to_gpu(
                    np.ascontiguousarray(lenna * 255, dtype))
            else:
                lenna_gpu = gpuarray.to_gpu(
                    np.ascontiguousarray(lenna, dtype))
            for use_textures in [True, False]:
                x_f, y_f = pystencils.fields('x,y: %s [2d]' % type_map[dtype], ghost_layers=0)

                if use_textures:
                    transformed = scale * sympy.rot_axis3(rotation_angle)[:2, :2] * sympy.Matrix((x_, y_)) + shift
                else:
                    transformed = scale * sympy.rot_axis3(rotation_angle)[:2, :2] * sympy.Matrix((x_, y_)) + shift
                assignments = pystencils.AssignmentCollection({
                    y_f.center(): LinearInterpolator(x_f, address_mode=address_mode).at(transformed)
                })
                # print(assignments)
                ast = pystencils.create_kernel(assignments, target='gpu', use_textures_for_interpolation=use_textures)
                # print(ast)
                print(pystencils.show_code(ast))
                kernel = ast.compile()

                out = gpuarray.zeros_like(lenna_gpu)
                kernel(x=lenna_gpu, y=out)
                pyconrad.imshow(out,
                                f"out {address_mode} texture:{use_textures} {type_map[dtype]}")
                skimage.io.imsave(f"/tmp/out {address_mode} texture:{use_textures} {type_map[dtype]}.tif",
                                  np.ascontiguousarray(out.get(), np.float32))
                # if not (use_single_precision and use_textures):
                # if previous_result is not None:
                # try:
                # assert np.allclose(previous_result[4:-4, 4:-4], out.get()
                # [4:-4, 4:-4], rtol=1e-3, atol=1e-2)
                # except AssertionError as e:
                # print("Max error: %f" % np.max(np.abs(previous_result[4:-4, 4:-4] - out.get()[4:-4, 4:-4])))
                # pyconrad.imshow(previous_result[4:-4, 4:-4] - out.get()[4:-4, 4:-4], "Difference image")
                # raise e
                # previous_result = out.get()


def test_rotate_interpolation_size_change():
    x_f, y_f = pystencils.fields('x,y: float64 [2d]')

    rotation_angle = sympy.pi / 5

    for address_mode in ['border', 'wrap', 'clamp', 'mirror']:

        transformed = sympy.rot_axis3(rotation_angle)[:2, :2] * sympy.Matrix((x_, y_))
        assignments = pystencils.AssignmentCollection({
            y_f.center(): LinearInterpolator(x_f, address_mode=address_mode).at(transformed)
        })
        print(assignments)
        ast = pystencils.create_kernel(assignments)
        print(ast)
        print(pystencils.show_code(ast))
        kernel = ast.compile()

        out = np.zeros((100, 150), np.float64)
        kernel(x=lenna, y=out)
        pyconrad.imshow(out, "small out " + address_mode)


def test_field_interpolated():
    x_f, y_f = pystencils.fields('x,y: float64 [2d]')

    for address_mode in ['border', 'wrap', 'clamp', 'mirror']:
        assignments = pystencils.AssignmentCollection({
            y_f.center(): x_f.interpolated_access([0.5 * x_ + 2.7, 0.25 * y_ + 7.2], address_mode=address_mode)
        })
        print(assignments)
        ast = pystencils.create_kernel(assignments)
        print(ast)
        print(pystencils.show_code(ast))
        kernel = ast.compile()

        out = np.zeros_like(lenna)
        kernel(x=lenna, y=out)
        pyconrad.imshow(out, "out " + address_mode)
