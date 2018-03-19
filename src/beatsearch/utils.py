# coding=utf-8
import os
import sys
import collections
import typing as tp
from time import time
from abc import ABCMeta, abstractmethod
from inspect import isclass
from functools import wraps, reduce
from matplotlib.colors import to_rgb, rgb_to_hsv, hsv_to_rgb, to_hex


def merge_dicts(x, y):
    z = x.copy()
    z.update(y)
    return z


def format_timespan(seconds):
    """
    Returns the given timespan as a string in hours:minutes:seconds.

    :param seconds: timespan in seconds
    :return: formatted string
    """

    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return "%d:%02d:%02d" % (h, m, s)


def print_progress_bar(iteration, total, prefix="", suffix="", decimals=1, length=50, fill='█', starting_time=None):
    """
    Call in a loop to create terminal progress bar

    :param iteration: current iteration
    :param total: total iterations
    :param prefix: prefix string
    :param suffix: suffix string
    :param decimals: number of decimals in percent progress
    :param length: character length of bar
    :param fill: bar fill character
    :param starting_time: starting time, for computing the time remaining for completion
    :return: terminal progress bar
    """

    progress = iteration / float(total)
    percent = ("{0:." + str(decimals) + "f}").format(100 * progress)
    filled_length = int(length * iteration // total)
    bar = fill * filled_length + '-' * (length - filled_length)

    if starting_time is not None:
        elapsed_time = time() - starting_time
        if iteration < total:
            try:
                remaining = format_timespan((elapsed_time / progress) - elapsed_time)
            except ZeroDivisionError:
                remaining = "unknown"
            time_info = "Remaining: %s " % remaining
        else:
            time_info = "Elapsed: %s" % format_timespan(elapsed_time)
    else:
        time_info = ""

    sys.stdout.write('\r%s |%s| %s%% %s %s' % (prefix, bar, percent, suffix, time_info))
    sys.stdout.flush()

    if iteration == total:
        print("")


def friendly_named_class(name):
    # noinspection PyPep8Naming
    def _friendly_named_class_decorator(Cls):
        setattr(Cls, '__friendly_name__', name)
        return Cls

    return _friendly_named_class_decorator


def err_print(*args, **kwargs):
    """
    Print function wrapper for printing to stderr.
    """

    print(*args, file=sys.stderr, **kwargs)


def make_dir_if_not_exist(directory):
    """
    Creates a directory if it doesn't exist yet.

    :param directory: path
    :return: path
    """

    if not os.path.isdir(directory):
        os.mkdir(directory)
    return directory


def head_trail_iter(iterable):
    """
    Returns an iterator which yields a tuple (is_first, is_last, item) on each iteration.

    :param iterable: iterable with length
    :return: iterator returning (is_first, is_last, item)
    """

    last_i = len(iterable) - 1
    for i, item in enumerate(iterable):
        first = i == 0
        last = i == last_i
        yield first, last, item


def get_beatsearch_dir(mkdir=True):
    """Returns the beatsearch directory
    Returns the beatsearch directory. This directory is used to store *.ini files and output various beatsearch output
    data.

    :param mkdir: when True, this function will create the beatsearch directory in case it does not exist
    :return: beatsearch directory path
    """

    home = os.path.expanduser("~")
    beatsearch_dir = os.path.join(home, "beatsearch")
    if not os.path.isdir(beatsearch_dir) and mkdir:
        os.mkdir(beatsearch_dir)
    return beatsearch_dir


def get_default_beatsearch_rhythms_fpath(mkdir=True):
    """Returns the default path to the rhythms pickle file

    Returns the default path to the rhythms pickle file. This path will be ${BSHome}/data/rhythms.pkl, where BSHome
    is the beatsearch directory returned by get_beatsearch_dir.

    :return: default path to the rhythms pickle file as a string
    """

    beatsearch_dir = get_beatsearch_dir(mkdir)
    return os.path.join(beatsearch_dir, "rhythms.pkl")


def no_callback(*_, **__):
    return None


def type_check_and_instantiate_if_necessary(thing, thing_base_type, allow_none=True, *args, **kwargs):
    """
    Instantiates the given 'thing' with the given args and kwargs and returns it. If the given 'thing' is already an
    instance of the given 'thing_base_type' it will return 'thing'.

    :param thing: either an instance or a (sub)-class of 'thing_base_type'
    :param thing_base_type: a class
    :param allow_none: when True, this function will allow None things (it will return None if 'thing' is None)
    :param args: if an initialization is needed, this positional arguments will be passed to the constructor
    :param kwargs: if an initialization is needed, this named arguments will be passed to the constructor
    :return: an instance of 'thing_base_type' (might return None if 'allow_none' is True)
    """
    # type: (tp.Any, tp.Type, bool) -> tp.Any

    if not isclass(thing_base_type):
        raise TypeError("Expected a class but got \"%s\"" % str(thing_base_type))

    if (allow_none and thing is None) or isinstance(thing, thing_base_type):
        return thing

    if not isclass(thing) or not issubclass(thing, thing_base_type):
        raise TypeError("Expected either a \"%s\" (sub)-class or "
                        "an instance but got \"%s\"" % (thing_base_type, str(thing)))

    return thing(*args, **kwargs)


def eat_args(func):
    """
    Adds ignored positional and named arguments to the given function's signature.

    :param func: function to add the fake arguments to
    :return: wrapped function
    """

    @wraps(func)
    def wrapper(*_, **__):
        return func()
    return wrapper


def color_variant(color, brightness_offset=0):
    """
    Takes a color and produces a lighter or darker variant.

    :param color: color to convert (either as a rgb array or as a hexadecimal string)
    :param brightness_offset: brightness offset between -1 and 1
    :return: new color (either hexadecimal or rgb depending on whether the input is a hexadecimal color)
    """

    try:
        is_hexadecimal = color.startswith("#")
    except TypeError:
        is_hexadecimal = False

    rgb = to_rgb(color)
    hsv = rgb_to_hsv(rgb)

    curr_brightness = hsv[2]
    new_brightness = max(-1.0, min(curr_brightness + brightness_offset, 1.0))  # clamp to [-1.0, 1.0]

    new_hsv = (hsv[0], hsv[1], new_brightness)
    new_rgb = hsv_to_rgb(new_hsv)

    if is_hexadecimal:
        return to_hex(new_rgb)

    return new_rgb


def get_midi_files_in_directory(directory):
    """Iterates over the midi files in the given directory

    Recursively iterates oer the midi files in the given directory yielding the full MIDI-file paths.

    :param directory: directory to iterate over
    :return: generator yielding paths to the MIDI files
    """

    for directory, subdirectories, files in os.walk(directory):
        for f_name in files:
            if os.path.splitext(f_name)[1] != ".mid":
                continue
            yield os.path.join(directory, directory, f_name)


class TupleView(collections.Sequence):
    @staticmethod
    def range_view(the_tuple, from_index, to_index):
        """Creates a range tuple view

        :param the_tuple: the tuple to create the view of
        :param from_index: the starting index
        :param to_index: the ending index (excluding)
        :return: TupleView
        """

        assert to_index >= from_index
        indices = range(from_index, to_index) if to_index > from_index else tuple()
        return TupleView(the_tuple, indices)

    def __init__(self, the_tuple, indices):
        """Creates a view of the given tuple

        :param the_tuple: the tuple to create the view of
        :param indices: the indices of the tuple
        """

        indices = tuple(indices)

        for ix in indices:
            try:
                the_tuple[ix]
            except IndexError:
                raise ValueError("invalid index: %i" % ix)

        self._the_tuple = the_tuple
        self._indices = indices

    def __getitem__(self, index):
        actual_index = self._indices[index]
        return self._the_tuple[actual_index]

    def __len__(self):
        return len(self._indices)


def most_common_element(sequence: tp.Sequence[tp.Any]) -> tp.Any:
    """Returns the most common element in the given sequence

    :param sequence: sequence
    :return: None
    """

    return max(set(sequence), key=sequence.count)


def sequence_product(iterable):
    """Returns the product of the given iterable

    :param iterable: iterable to return the product of
    :return: product of given iterable
    """

    return reduce((lambda x, y: x * y), iterable)


class Quantizable(object, metaclass=ABCMeta):
    @property
    def quantize_enabled(self) -> bool:
        return self.is_quantize_enabled()

    @quantize_enabled.setter
    def quantize_enabled(self, quantize_enabled: bool):
        self.set_quantize_enabled(quantize_enabled)

    @abstractmethod
    def set_quantize_enabled(self, quantize_enabled: bool):
        raise NotImplementedError

    @abstractmethod
    def is_quantize_enabled(self) -> bool:
        raise NotImplementedError


__all__ = [
    'merge_dicts', 'format_timespan', 'print_progress_bar', 'friendly_named_class',
    'err_print', 'make_dir_if_not_exist', 'head_trail_iter', 'get_beatsearch_dir',
    'get_default_beatsearch_rhythms_fpath', 'no_callback', 'type_check_and_instantiate_if_necessary',
    'eat_args', 'color_variant', 'get_midi_files_in_directory', 'TupleView', 'most_common_element',
    'sequence_product', 'Quantizable'
]