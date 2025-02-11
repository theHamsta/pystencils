import base64
from tempfile import NamedTemporaryFile

import matplotlib.animation as animation
import sympy as sp
from IPython.display import HTML

import pystencils.plot as plt

__all__ = ['log_progress', 'make_imshow_animation', 'display_animation', 'set_display_mode']


def log_progress(sequence, every=None, size=None, name='Items'):
    """Copied from https://github.com/alexanderkuk/log-progress"""
    from ipywidgets import IntProgress, HTML, VBox
    from IPython.display import display

    is_iterator = False
    if size is None:
        try:
            size = len(sequence)
        except TypeError:
            is_iterator = True
    if size is not None:
        if every is None:
            if size <= 200:
                every = 1
            else:
                every = int(size / 200)     # every 0.5%
    else:
        assert every is not None, 'sequence is iterator, set every'

    if is_iterator:
        progress = IntProgress(min=0, max=1, value=1)
        progress.bar_style = 'info'
    else:
        progress = IntProgress(min=0, max=size, value=0)
    label = HTML()
    box = VBox(children=[label, progress])
    display(box)

    index = 0
    try:
        for index, record in enumerate(sequence, 1):
            if index == 1 or index % every == 0:
                if is_iterator:
                    label.value = '{name}: {index} / ?'.format(
                        name=name,
                        index=index
                    )
                else:
                    progress.value = index
                    label.value = u'{name}: {index} / {size}'.format(
                        name=name,
                        index=index,
                        size=size
                    )
            yield record
    except:
        progress.bar_style = 'danger'
        raise
    else:
        progress.bar_style = 'success'
        progress.value = index
        label.value = "{name}: {index}".format(
            name=name,
            index=str(index or '?')
        )


VIDEO_TAG = """<video controls width="80%">
 <source src="data:video/x-m4v;base64,{0}" type="video/mp4">
 Your browser does not support the video tag.
</video>"""


def __animation_to_html(animation, fps):
    if not hasattr(animation, 'encoded_video'):
        with NamedTemporaryFile(suffix='.mp4') as f:
            animation.save(f.name, fps=fps, extra_args=['-vcodec', 'libx264', '-pix_fmt',
                                                        'yuv420p', '-profile:v', 'baseline', '-level', '3.0'])
            video = open(f.name, "rb").read()
        animation.encoded_video = base64.b64encode(video).decode('ascii')

    return VIDEO_TAG.format(animation.encoded_video)


def make_imshow_animation(grid, grid_update_function, frames=90, **_):
    from functools import partial
    fig = plt.figure()
    im = plt.imshow(grid, interpolation='none')

    def update_figure(*_, **kwargs):
        image = kwargs['image']
        image = grid_update_function(image)
        im.set_array(image)
        return im,

    return animation.FuncAnimation(fig, partial(update_figure, image=grid), frames=frames)


# -------   Version 1: Embed the animation as HTML5 video --------- ----------------------------------

def display_as_html_video(animation, fps=30, show=True, **_):
    try:
        plt.close(animation._fig)
        res = __animation_to_html(animation, fps)
        if show:
            return HTML(res)
        else:
            return HTML("")
    except KeyboardInterrupt:
        pass


# -------   Version 2: Animation is shown in extra matplotlib window ----------------------------------


def display_in_extra_window(*_, **__):
    fig = plt.gcf()
    try:
        fig.canvas.manager.window.raise_()
    except Exception:
        pass
    plt.show()


# -------   Version 3: Animation is shown in images that are updated directly in website --------------

def display_as_html_image(animation, show=True, *args, **kwargs):
    from IPython import display

    try:
        if show:
            animation._init_draw()
        for _ in animation.frame_seq:
            if show:
                fig = plt.gcf()
                display.display(fig)
            animation._step()
            if show:
                display.clear_output(wait=True)
    except KeyboardInterrupt:
        display.clear_output(wait=False)


# Dispatcher

animation_display_mode = 'image_update'
display_animation_func = None


def display_animation(*args, **kwargs):
    from IPython import get_ipython
    ipython = get_ipython()
    if not ipython:
        return

    if not display_animation_func:
        raise Exception("Call set_display_mode first")
    return display_animation_func(*args, **kwargs)


def set_display_mode(mode):
    from IPython import get_ipython
    ipython = get_ipython()
    if not ipython:
        return
    global animation_display_mode
    global display_animation_func
    animation_display_mode = mode
    if animation_display_mode == 'video':
        ipython.magic("matplotlib inline")
        display_animation_func = display_as_html_video
    elif animation_display_mode == 'window':
        ipython.magic("matplotlib qt")
        display_animation_func = display_in_extra_window
    elif animation_display_mode == 'image_update':
        ipython.magic("matplotlib inline")
        display_animation_func = display_as_html_image
    else:
        raise Exception("Unknown mode. Available modes 'image_update', 'video' and 'window' ")


def activate_ipython():
    from IPython import get_ipython
    ipython = get_ipython()
    if ipython:
        set_display_mode('image_update')
        ipython.magic("config InlineBackend.rc = { }")
        ipython.magic("matplotlib inline")
        plt.rc('figure', figsize=(16, 6))
        sp.init_printing()


activate_ipython()
