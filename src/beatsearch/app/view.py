import os
import uuid
import signal
import textwrap
import tkinter.ttk
from abc import ABCMeta, abstractmethod
import tkinter as tk
import tkinter.font
import tkinter.filedialog
from tkinter import messagebox
from contextlib import contextmanager
from itertools import zip_longest, repeat
from tkinter import ttk
from tkinter import filedialog
import matplotlib
matplotlib.use("TkAgg")
from matplotlib import pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from functools import wraps, partial
import typing as tp
from collections import OrderedDict
from beatsearch.rhythm import Unit, get_drum_mapping_reducer_implementation_names
from beatsearch.metrics import MonophonicRhythmDistanceMeasure, TRACK_WILDCARDS
from beatsearch.utils import (
    head_trail_iter,
    no_callback,
    type_check_and_instantiate_if_necessary,
    eat_args,
    color_variant,
    normalize_directory
)
from beatsearch.app.control import BSController, BSMidiRhythmLoader
from beatsearch.plot import RhythmLoopPlotter, SnapsToGridPolicy, get_rhythm_loop_plotter_classes
from beatsearch.rhythm import (
    RhythmLoop,
    MidiRhythm,
    Rhythm,
    MidiDrumMappingReducer,
    get_drum_mapping_reducer_implementation_friendly_names,
    get_drum_mapping_reducer_implementation,
)
from beatsearch.app.config import BSConfig


UNITS_BY_UNIT_TITLES = OrderedDict((u.get_note_names()[0].capitalize(), u) for u in Unit)  # type: tp.Dict[str, Unit]
UNIT_TITLES_BY_UNITS = OrderedDict((u, t) for t, u in UNITS_BY_UNIT_TITLES.items())  # type: tp.Dict[Unit, str]
UNIT_TITLES = tuple(UNITS_BY_UNIT_TITLES.keys())


class BSAppWidgetMixin(object):
    def __init__(self, app):
        if not isinstance(app, BSApp):
            raise TypeError("Expected a BSApp but got \"%s\"'" % app)
        self.controller = app.controller
        self.dpi = app.winfo_fpixels("1i")


class BSAppFrame(tk.Frame, BSAppWidgetMixin):
    def __init__(self, app, **kwargs):
        tk.Frame.__init__(self, master=app, **kwargs)
        BSAppWidgetMixin.__init__(self, app)


class BSAppTtkFrame(ttk.Frame, BSAppWidgetMixin):
    def __init__(self, app, **kwargs):
        ttk.Frame.__init__(self, master=app, **kwargs)
        BSAppWidgetMixin.__init__(self, app)


class BSAppWindow(tk.Toplevel, BSAppWidgetMixin):
    def __init__(self, app, cnf=None, **kwargs):
        tk.Toplevel.__init__(self, master=app, cnf=cnf or dict(), **kwargs)
        BSAppWidgetMixin.__init__(self, app)
        self.bind("<Escape>", eat_args(self.destroy))


class BSMidiFileRhythmLoader(BSMidiRhythmLoader):
    SOURCE_NAME = "MIDI file"

    def __init__(self):
        super().__init__()

    @classmethod
    def get_source_name(cls):
        return cls.SOURCE_NAME

    def is_available(self):
        return True

    def __load__(self, rhythm_resolution: int,
                 mapping_reducer: tp.Optional[tp.Type[MidiDrumMappingReducer]]) -> MidiRhythm:
        fpath = filedialog.askopenfilename(
            title="Load rhythm from MIDI file",
            filetypes=(("MIDI files", "*.mid"), ("All files", "*"))
        )
        if not fpath:
            # TODO Add mechanism to cancel the loading process without raising a LoadingError (extra "cancel" arg?)
            return None
        if not os.path.isfile(fpath):
            raise self.LoadingError("No such file: %s" % fpath)
        try:
            rhythm = MidiRhythm(fpath, midi_mapping_reducer_cls=mapping_reducer)
            rhythm.set_resolution(rhythm_resolution)
        except TypeError as e:
            fname = os.path.basename(fpath)
            raise self.LoadingError("Error loading\"%s\": %s" % (fname, str(e)))
        return rhythm


class BSSearchForm(BSAppFrame):
    INNER_PAD_X = 6

    COMBO_DISTANCE_MEASURE = "<Combo-DistanceMeasure>"
    COMBO_TRACKS = "<Combo-Tracks>"
    COMBO_QUANTIZE = "<Combo-Quantize>"

    def __init__(self, controller, background=None, **kwargs):
        BSAppFrame.__init__(self, controller, background=background, **kwargs)

        combobox_info = [
            (self.COMBO_DISTANCE_MEASURE, "Distance measure", MonophonicRhythmDistanceMeasure.get_measure_names()),
            (self.COMBO_TRACKS, "Tracks to compare", TRACK_WILDCARDS),
            (self.COMBO_QUANTIZE, "Quantize", UNIT_TITLES)
        ]

        widgets = []
        self._combo_boxes = {}

        for is_first, is_last, [name, text, values] in head_trail_iter(combobox_info):
            box_container = tk.Frame(self)

            # setup combobox
            combobox = tkinter.ttk.Combobox(box_container, values=values, state="readonly")
            combobox.current(0)
            combobox.pack(side=tk.BOTTOM, fill=tk.X, expand=True)
            self._combo_boxes[name] = combobox

            # setup label
            box_label = tk.Label(box_container, text=text, anchor=tk.W, bg=background)
            box_label.pack(side=tk.TOP, fill=tk.X, expand=True)

            widgets.append(box_container)

        self._btn_search = tk.Button(self, text="Search")
        widgets.append(self._btn_search)

        for is_first, is_last, w in head_trail_iter(widgets):
            w.pack(side=tk.LEFT, padx=(
                BSSearchForm.INNER_PAD_X if is_first else BSSearchForm.INNER_PAD_X / 2.0,
                BSSearchForm.INNER_PAD_X if is_last else BSSearchForm.INNER_PAD_X / 2.0
            ), fill=tk.BOTH, expand=True)

        self._callbacks = dict((combo_name, no_callback) for combo_name in [
            self.COMBO_DISTANCE_MEASURE,
            self.COMBO_TRACKS,
            self.COMBO_QUANTIZE
        ])

    def redraw(self):
        controller = self.controller
        q_combo = self._combo_boxes[self.COMBO_QUANTIZE]
        q_combo.config(state=tk.NORMAL if controller.is_current_distance_measure_quantizable() else tk.DISABLED)
        self._btn_search.config(state=tk.NORMAL if controller.is_target_rhythm_set() else tk.DISABLED)

    @property
    def on_new_measure(self):  # type: () -> tp.Callable[str]
        return self._callbacks[self.COMBO_DISTANCE_MEASURE]

    @property
    def on_new_tracks(self):  # type: () -> tp.Callable[str]
        return self._callbacks[self.COMBO_TRACKS]

    @property
    def on_new_quantize(self):  # type: () -> tp.Callable[str]
        return self._callbacks[self.COMBO_QUANTIZE]

    @on_new_measure.setter
    def on_new_measure(self, callback):
        self._set_combobox_value_callback(self.COMBO_DISTANCE_MEASURE, callback)

    @on_new_tracks.setter
    def on_new_tracks(self, callback):
        self._set_combobox_value_callback(self.COMBO_TRACKS, callback)

    @on_new_quantize.setter
    def on_new_quantize(self, callback):
        self._set_combobox_value_callback(self.COMBO_QUANTIZE, callback)

    def _set_combobox_value_callback(self, combobox_name, callback):
        # type: (str, tp.Callable[str]) -> None
        @wraps(callback)
        def wrapper(event):
            value = event.widget.get()
            return callback(value)

        combobox = self._combo_boxes[combobox_name]
        combobox.bind("<<ComboboxSelected>>", wrapper)

    @property
    def search_command(self):
        return self._btn_search.cget("command")

    @search_command.setter
    def search_command(self, callback):
        if not callable(callback):
            raise TypeError("Expected callable but got '%s'" % str(callback))
        self._btn_search.configure(command=callback)


class ContextMenu(tk.Menu):
    def __init__(self, master, event_show="<Button-3>", enabled: bool = True, **kwargs):
        tk.Menu.__init__(self, master, tearoff=0, **kwargs)
        self._event_show = event_show
        self._event_func_id = None
        self.set_enabled(enabled)

        self._event_func_id = master.bind(event_show, lambda event: self.show(event.x_root, event.y_root))

    def show(self, x, y):
        try:
            self.tk_popup(x, y, 0)
        finally:
            self.grab_release()

    def add_submenu(self, label: str) -> tk.Menu:
        submenu = tk.Menu(tearoff=0)
        self.add_cascade(label=label, menu=submenu)
        return submenu

    def is_enabled(self) -> bool:
        return bool(self._event_func_id)

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self.is_enabled():
            return

        master = self.master
        event = self._event_show

        if enabled:
            self._event_func_id = master.bind(event, lambda e: self.show(e.x_root, e.y_root))
        else:
            master.unbind(event, self._event_func_id)
            self._event_func_id = None


class BSRhythmList(BSAppFrame):

    def __init__(self, app, h_scroll=False, v_scroll=True, background="white", **kwargs):
        BSAppFrame.__init__(self, app, **kwargs)
        column_headers = BSController.get_rhythm_data_attr_names()
        tv_container = tk.Frame(self, bd=1, relief=tk.FLAT, bg="gray")
        tree_view = tkinter.ttk.Treeview(tv_container, columns=column_headers, show="headings")

        # set treeview background
        style = ttk.Style(self)
        style.layout("Treeview", [('Treeview.treearea', {'sticky': 'nswe'})])  # get rid of tree-view border
        style.configure("Treeview", background=background)

        scrollbars = {
            tk.X: tkinter.ttk.Scrollbar(self, orient="horizontal", command=tree_view.xview),
            tk.Y: tkinter.ttk.Scrollbar(self, orient="vertical", command=tree_view.yview)
        }

        if h_scroll:
            scrollbars[tk.X].pack(side=tk.BOTTOM, fill=tk.X)

        if v_scroll:
            scrollbars[tk.Y].pack(side=tk.RIGHT, fill=tk.Y)

        tree_view.configure(xscrollcommand=scrollbars[tk.X].set)
        tree_view.configure(yscrollcommand=scrollbars[tk.Y].set)
        tree_view.pack(fill=tk.BOTH, expand=True)
        tv_container.pack(fill=tk.BOTH, expand=True)

        # set treeview column headers
        for col in column_headers:
            tree_view.heading(col, text=col, command=lambda c=col: self._sort_tree_view(c, False))
            tree_view.column(col, width=tkinter.font.Font().measure(col))  # adjust column width to header string

        # right-click menu
        context_menu = ContextMenu(tree_view)
        context_menu.add_command(label="Set as target rhythm", command=self._on_set_as_target_rhythm)
        context_menu.add_command(label="Export as MIDI", command=self._on_export_as_midi_rhythm)

        # bind events
        self.bind("<MouseWheel>", self._on_mousewheel)
        tree_view.bind("<<TreeviewSelect>>", self._on_tree_select)
        tree_view.bind("<Double-Button-1>", self._on_double_click)

        # attributes
        self._on_request_target_rhythm = no_callback          # type: tp.Callable[[int], tp.Any]
        self._on_request_export_rhythm_as_midi = no_callback  # type: tp.Callable[[int, str], tp.Any]
        self._tree_view = tree_view                           # type: tkinter.ttk.Treeview
        self._context_menu = context_menu                     # type: ContextMenu
        self._corpus_id = None                                # type: tp.Union[uuid.UUID, None]

    def redraw(self):
        controller = self.controller

        if self._corpus_changed():
            self._clear_tree_view()
            self._fill_tree_view()
            return

        rhythm_data = tuple(controller.get_rhythm_data())

        tv = self._tree_view
        for item_iid in tv.get_children():
            rhythm_ix = self._get_rhythm_index(item_iid)
            tv.item(item_iid, values=rhythm_data[rhythm_ix])

        self._sort_tree_view("Distance to target", 0)

    @property
    def on_request_target_rhythm(self) -> tp.Callable[[int], tp.Any]:
        return self._on_request_target_rhythm

    @on_request_target_rhythm.setter
    def on_request_target_rhythm(self, callback: tp.Callable[[int], tp.Any]):
        if not callable(callback):
            raise TypeError("Expected a callback but got \"%s\"" % str(callback))
        self._on_request_target_rhythm = callback

    @property
    def on_request_export_rhythm_as_midi(self) -> tp.Callable[[int, str], tp.Any]:
        return self._on_request_export_rhythm_as_midi

    @on_request_export_rhythm_as_midi.setter
    def on_request_export_rhythm_as_midi(self, callback: tp.Callable[[int, str], tp.Any]):
        if not callable(callback):
            raise TypeError("Expected a callback but got \"%s\"" % str(callback))
        self._on_request_export_rhythm_as_midi = callback

    def _on_set_as_target_rhythm(self):
        selected_rhythms = self._get_selected_rhythm_indices()
        assert len(selected_rhythms) >= 1
        self.on_request_target_rhythm(selected_rhythms[0])

    def _on_export_as_midi_rhythm(self):
        tree = self._tree_view
        tv_selection = tree.selection()
        assert len(tv_selection) == 1
        rhythm_ix, rhythm_name = next((self._get_rhythm_index(_id), self._get_rhythm_name(_id)) for _id in tv_selection)
        self.on_request_export_rhythm_as_midi(rhythm_ix, "%s.mid" % rhythm_name)

    def _fill_tree_view(self):
        controller = self.controller
        for item in controller.get_rhythm_data():
            self._tree_view.insert("", tk.END, values=item)
        self._corpus_id = controller.get_corpus_id()

    def _clear_tree_view(self):
        tv = self._tree_view
        tv.delete(*tv.get_children())

    def _corpus_changed(self):
        corpus_id = self.controller.get_corpus_id()
        return self._corpus_id != corpus_id

    def _on_mousewheel(self, event):
        self._tree_view.yview_scroll(-1 * (event.delta // 15), tk.UNITS)

    def _get_rhythm_index(self, tree_view_item_iid):
        tv = self._tree_view
        tv_item = tv.item(tree_view_item_iid)
        tv_values = tv_item['values']
        name_ix = BSController.get_rhythm_data_attr_names().index("I")
        return tv_values[name_ix]

    def _get_rhythm_name(self, tree_view_item_iid):
        tv = self._tree_view
        tv_item = tv.item(tree_view_item_iid)
        tv_values = tv_item['values']
        name_ix = BSController.get_rhythm_data_attr_names().index("Name")
        return tv_values[name_ix]

    def _get_selected_rhythm_indices(self):
        tv_selection = self._tree_view.selection()
        return [self._get_rhythm_index(item_iid) for item_iid in tv_selection]

    def _on_tree_select(self, _):
        controller = self.controller
        selected_rhythms = self._get_selected_rhythm_indices()
        controller.set_rhythm_selection(selected_rhythms)
        # enable context menu only when just one rhythm is selected
        self._context_menu.set_enabled(len(selected_rhythms) == 1)

    def _on_double_click(self, _):
        selected_rhythms = self._get_selected_rhythm_indices()
        assert len(selected_rhythms) == 1
        self.on_request_target_rhythm(selected_rhythms[0])

    def _sort_tree_view(self, column, descending):  # sorts the rhythms by the given column
        tv = self._tree_view
        data_type = BSController.RHYTHM_DATA_TYPES[column]
        data = [(tv.set(item_iid, column), item_iid) for item_iid in tv.get_children("")]
        data.sort(reverse=descending, key=lambda cell: data_type(cell[0]))
        for i, row_info in enumerate(data):
            tv.move(row_info[1], "", i)
        tv.heading(column, command=lambda col=column: self._sort_tree_view(col, not descending))


class BSTransportControls(BSAppFrame, object):
    def __init__(self, controller, background="#37474F", **kwargs):
        BSAppFrame.__init__(self, controller, background=background, **kwargs)
        self._btn_toggle_play = ToggleButton(self, text=("Play", "Stop"), width=8)
        self._btn_toggle_play.pack(side=tk.LEFT, padx=6, pady=6)
        self._play_command = None

    def redraw(self):
        controller = self.controller
        rhythm_selection = controller.get_rhythm_selection()
        is_playing = controller.is_rhythm_player_playing()
        btn = self._btn_toggle_play
        btn.set_enabled(len(rhythm_selection) > 0)
        btn.set_toggle(is_playing)

    @property
    def toggle_play_command(self):
        return self._btn_toggle_play.cget("command")

    @toggle_play_command.setter
    def toggle_play_command(self, callback):
        if not callable(callback):
            raise TypeError("Expected callable but got '%s'" % str(callback))
        self._btn_toggle_play.configure(command=callback)


class BSRhythmComparisonStrip(BSAppTtkFrame):
    RHYTHM_PLOTTERS = tuple(cls() for cls in get_rhythm_loop_plotter_classes())  # type: tp.Tuple[RhythmLoopPlotter]
    PLOT_TYPE_LABELS = tuple(plotter.get_plot_type_name() for plotter in RHYTHM_PLOTTERS)

    RHYTHM_PLOTTERS_BY_LABELS = OrderedDict(tuple(zip(PLOT_TYPE_LABELS, RHYTHM_PLOTTERS)))
    LABELS_BY_RHYTHM_PLOTTERS = OrderedDict(tuple(zip(RHYTHM_PLOTTERS, PLOT_TYPE_LABELS)))

    def __init__(self, app, background_left="#E0E0E0", background_right="#EEEEEE", **kwargs):
        super().__init__(app, **kwargs)
        self.rhythm_plotter = self.RHYTHM_PLOTTERS[0]
        self._var_snap_to_grid = tk.BooleanVar()
        self._var_snap_to_grid.trace("w", self._on_btn_snap_to_grid)
        self._prev_adjustable_snap_to_grid = -1
        self._rhythm_plot_unit = Unit.get(self.rhythm_plotter.unit)

        left_header, left_panel, rhythm_menu = self._create_target_rhythm_frame(background_left)
        right_header, right_panel, btn_snap_to_grid = self._create_selected_rhythms_frame(background_right)

        left_panel.grid(row=1, column=0, sticky="nsew")
        left_header.grid(row=0, column=0, sticky="nsew")

        right_panel.grid(row=1, column=1, sticky="nsew")
        right_header.grid(row=0, column=1, sticky="nsew")
        self.grid_columnconfigure(1, weight=1)  # expand horizontally

        self._frame_target = left_panel
        self._frame_selection = right_panel

        self._target_rhythm_menu = rhythm_menu
        self._target_rhythm_menu_item_count = 0
        self._btn_snap_to_grid = btn_snap_to_grid
        self.update_snap_to_grid_btn_state()

    class RhythmPlottingCanvas(FigureCanvasTkAgg, object):
        def __init__(self, master, dpi, background="white", figsize=(3, 3), **kwargs):
            figure = plt.Figure(dpi=dpi, figsize=figsize, facecolor=background)
            self.background = background
            super().__init__(figure=figure, master=master, **kwargs)

        @property
        def widget(self):
            return self.get_tk_widget()

        @contextmanager
        def figure_update(self):
            figure = self.figure
            figure.clear()
            figure.set_facecolor(self.background)
            yield figure
            self.draw()

        def reset(self):
            with self.figure_update() as _:
                pass

    class SelectedRhythmsFrame(tk.Frame):
        def __init__(self, root, n_boxes=20, dpi=100, background=None, **kwargs):
            super().__init__(root, background=background, **kwargs)
            scrolled_frame = HorizontalScrolledFrame(self, bg=background)
            self._boxes = tuple(box(scrolled_frame.interior, dpi, background=background)
                                for box in repeat(self.SelectedRhythmBox, n_boxes))
            # type: tp.Tuple[self.SelectedRhythmBox, ...]

            for box in self._boxes:
                box.on_mousewheel = self._handle_canvas_mousewheel

            scrolled_frame.pack(expand=True, fill=tk.BOTH)
            self._scrolled_frame = scrolled_frame

        class SelectedRhythmBox(tk.Frame, object):
            def __init__(self, master, dpi, background="white", **kwargs):
                super().__init__(master, background=background, **kwargs)
                self._plot = BSRhythmComparisonStrip.RhythmPlottingCanvas(self, dpi, background)
                self._prev_redraw_args = ()
                self._plot.widget.pack(expand=True, fill=tk.BOTH)
                self._mousewheel_callback = None
                self._mousewheel_callback_id = None

            def redraw(self, rhythm_loop: RhythmLoop, plotter: RhythmLoopPlotter, force_redraw=False):
                # plot_function should be a @plot decorated RhythmLoopPlotter method
                if not force_redraw and (rhythm_loop, plotter, plotter.snaps_to_grid, plotter.unit) == \
                        self._prev_redraw_args:
                    return

                with self._plot.figure_update() as figure:
                    if rhythm_loop is not None:
                        plotter.draw(rhythm_loop, figure=figure)
                        figure.suptitle(rhythm_loop.name, fontsize="small", y=0.13)

                self._prev_redraw_args = (rhythm_loop, plotter, plotter.snaps_to_grid, plotter.unit)

            @property
            def on_mousewheel(self):
                return self._mousewheel_callback

            @on_mousewheel.setter
            def on_mousewheel(self, callback):
                canvas = self._plot.figure.canvas

                if callback is None and self._mousewheel_callback_id is not None:
                    canvas.mpl_disconnect(self._mousewheel_callback_id)
                    self._mousewheel_callback_id = None
                    self._mousewheel_callback = None
                    return

                if not callable(callback):
                    raise TypeError("Expected callable but got \"%s\"" % callback)

                self._mousewheel_callback = callback
                cid = canvas.mpl_connect("scroll_event", callback)
                self._mousewheel_callback_id = cid

        def redraw(self, rhythms: tp.Iterable[Rhythm], plotter: RhythmLoopPlotter, force_redraw=False):
            n_boxes = len(self._boxes)
            is_first = True

            for i, [box, rhythm] in enumerate(zip_longest(self._boxes, rhythms)):
                if i >= n_boxes:
                    break

                box.redraw(rhythm, plotter, force_redraw)

                # Pack at least one box in order to stretch the parent's y
                # to the correct size
                if is_first or rhythm is not None:
                    box.pack(side=tk.LEFT)
                else:
                    box.pack_forget()

                is_first = False

        def _handle_canvas_mousewheel(self, event):
            scrolled_frame = self._scrolled_frame
            right = event.button == "up"
            scrolled_frame.xview_scroll(1 if right else -1, tk.UNITS)

    class TargetRhythmBox(tk.Frame):
        def __init__(self, master, dpi, background=None, **kwargs):
            super().__init__(master, background=background, **kwargs)
            container = tk.Frame(self)
            container.pack(side="top", fill="both", expand=True)
            container.grid_rowconfigure(0, weight=1)
            container.grid_columnconfigure(0, weight=1)

            self._screen_no_target = tk.Label(container, text="No target rhythm set", bg=background)
            self._screen_main = tk.Frame(container, bg=background)

            plot_canvas = BSRhythmComparisonStrip.RhythmPlottingCanvas(self._screen_main, dpi, background)
            plot_canvas.widget.pack()
            self._plot_canvas = plot_canvas

            for screen in [self._screen_no_target, self._screen_main]:
                screen.grid(row=0, column=0, sticky="nsew")

            container.pack(padx=3)

        def redraw(self, rhythm_loop: RhythmLoop, plotter: RhythmLoopPlotter):
            with self._plot_canvas.figure_update() as figure:
                figure.clear()
                if rhythm_loop is not None:
                    plotter.draw(rhythm_loop, figure=figure)
                    # plotter(rhythm_loop, snap_to_grid=snap_to_grid, figure=figure)
                    figure.suptitle(rhythm_loop.name, fontsize="small", y=0.13)
                    self._screen_main.tkraise()
                else:
                    self._screen_no_target.tkraise()

    def redraw(self):
        self.redraw_rhythm_plots()
        self.redraw_target_rhythm_menu()

    def set_rhythm_plotter(self, rhythm_plotter: RhythmLoopPlotter):
        self.rhythm_plotter = rhythm_plotter
        self.update_snap_to_grid_btn_state()
        self.redraw_rhythm_plots()

    def update_snap_to_grid_btn_state(self):
        snaps_to_grid_policy = self.rhythm_plotter.snap_to_grid_policy
        btn_snap_to_grid = self._btn_snap_to_grid
        var_snap_to_grid = self._var_snap_to_grid

        # force the "snap to grid" checkbox state if the current plot type is
        # not adjustable, otherwise restore the last manually adjusted state
        if snaps_to_grid_policy == SnapsToGridPolicy.ADJUSTABLE:
            btn_snap_to_grid.config(state=tk.NORMAL)
            if self._prev_adjustable_snap_to_grid >= 0:
                var_snap_to_grid.set(self._prev_adjustable_snap_to_grid)
                self._prev_adjustable_snap_to_grid = -1
        else:
            self._prev_adjustable_snap_to_grid = var_snap_to_grid.get()
            var_snap_to_grid.set(snaps_to_grid_policy == SnapsToGridPolicy.ALWAYS)
            btn_snap_to_grid.config(state=tk.DISABLED)

    def set_rhythm_plot_unit(self, unit: Unit):
        self.rhythm_plotter.unit = unit
        # the plot function didn't change and the selected rhythms won't redraw,
        # that's why we force redraw
        self.redraw_rhythm_plots(force_redraw=True)

    def redraw_rhythm_plots(self, force_redraw=False):
        controller = self.controller
        rhythm_plotter = self.rhythm_plotter

        target_rhythm_frame = self._frame_target
        target_rhythm = controller.get_target_rhythm()
        target_rhythm_frame.redraw(target_rhythm, rhythm_plotter)

        rhythm_selection_frame = self._frame_selection
        rhythm_selection = controller.get_rhythm_selection()
        rhythm_selection_frame.redraw(
            iter(controller.get_rhythm_by_index(ix) for ix in rhythm_selection),
            rhythm_plotter, force_redraw
        )

    def _create_target_rhythm_frame(self, background_color):
        header = tk.Frame(self, bg=background_color)
        title = tk.Label(header, text="Target Rhythm", anchor=tk.W, font=BSApp.FONT['header'], bg=background_color)

        btn_load_target_rhythm = tk.Menubutton(header, text="load", relief=tk.FLAT, borderwidth=0)
        btn_load_target_rhythm.bind("<Button-1>", eat_args(self.redraw_target_rhythm_menu))

        rhythm_menu = tk.Menu(btn_load_target_rhythm, tearoff=0)
        btn_load_target_rhythm['menu'] = rhythm_menu

        title.pack(side=tk.LEFT, padx=3)
        btn_load_target_rhythm.pack(side=tk.RIGHT, padx=4, pady=4)

        return header, self.TargetRhythmBox(self, self.dpi, background=background_color), rhythm_menu

    def _create_selected_rhythms_frame(self, background_color):
        header = tk.Frame(self, bg=background_color)
        title = tk.Label(header, text="Selected Rhythms", anchor=tk.W, font=BSApp.FONT['header'], bg=background_color)

        label_plot_type = tk.Label(header, text="Plot type", bg=background_color)
        plot_type_labels = self.PLOT_TYPE_LABELS
        combo_plot_type = tkinter.ttk.Combobox(header, values=plot_type_labels, state="readonly")
        combo_plot_type.set(self.LABELS_BY_RHYTHM_PLOTTERS[self.rhythm_plotter])
        combo_plot_type.bind("<<ComboboxSelected>>", self._on_plot_type_combobox)

        combo_plot_unit = tkinter.ttk.Combobox(header, values=UNIT_TITLES, state="readonly")
        combo_plot_unit.set(self._rhythm_plot_unit.name)
        combo_plot_unit.bind("<<ComboboxSelected>>", self._on_plot_unit_combobox)

        check_btn_snap_to_grid = tk.Checkbutton(header, text="Snap to grid", variable=self._var_snap_to_grid)

        title.pack(side=tk.LEFT, padx=3)
        check_btn_snap_to_grid.pack(side=tk.RIGHT, padx=(0, 6))
        combo_plot_unit.pack(side=tk.RIGHT, padx=(0, 6))
        combo_plot_type.pack(side=tk.RIGHT, padx=(0, 6))
        label_plot_type.pack(side=tk.RIGHT, fill=tk.Y, padx=6)

        return header, self.SelectedRhythmsFrame(
            self, n_boxes=19, dpi=self.dpi, background=background_color), check_btn_snap_to_grid

    def _on_plot_type_combobox(self, event):
        value = event.widget.get()
        plotter = self.RHYTHM_PLOTTERS_BY_LABELS[value]
        self.set_rhythm_plotter(plotter)

    def _on_plot_unit_combobox(self, event):
        unit_title = event.widget.get()
        unit = UNITS_BY_UNIT_TITLES[unit_title]
        self.set_rhythm_plot_unit(unit)

    def _on_rhythm_load(self, source_type):
        controller = self.controller
        loader = controller.get_rhythm_loader(source_type)
        rhythm = loader.load()
        if rhythm is not None:
            controller.set_target_rhythm(rhythm)

    def _on_btn_snap_to_grid(self, *_):
        rhythm_plotter = self.rhythm_plotter
        snaps_to_grid_policy = rhythm_plotter.snap_to_grid_policy

        if snaps_to_grid_policy == SnapsToGridPolicy.ADJUSTABLE:
            rhythm_plotter.snaps_to_grid = self._var_snap_to_grid.get()
            self.redraw_rhythm_plots(force_redraw=True)

    def redraw_target_rhythm_menu(self):
        controller = self.controller
        menu = self._target_rhythm_menu

        # reset menu
        menu.delete(0, self._target_rhythm_menu_item_count)
        self._target_rhythm_menu_item_count = 0

        for rhythm_loader_type, rhythm_loader in controller.get_rhythm_loader_iterator():
            rhythm_source_name = rhythm_loader.get_source_name()
            label = "From %s..." % rhythm_source_name
            menu.add_command(
                label=label,
                command=partial(self._on_rhythm_load, rhythm_loader_type),
                state=tk.NORMAL if rhythm_loader.is_available() else tk.DISABLED
            )
            self._target_rhythm_menu_item_count += 1


class BSMainMenu(tk.Menu, object):
    def __init__(self, root, **kwargs):
        tk.Menu.__init__(self, root, **kwargs)
        f_menu = tk.Menu(self, tearoff=0)
        f_menu.add_command(
            label="New rhythm",
            command=lambda: self.on_request_new_rhythm_window(),
            accelerator="Ctrl+N"
        )
        f_menu.add_command(
            label="Settings",
            command=lambda: self.on_request_show_settings_window(),
            accelerator="Ctrl+,"
        )
        f_menu.add_command(
            label="MIDI Batch Export",
            command=lambda: self.on_request_show_midi_batch_export_window(),
            accelerator="Ctrl+Shift+E"
        )
        f_menu.add_separator()
        f_menu.add_command(
            label="Exit",
            command=lambda: self.on_request_exit()
        )
        self.add_cascade(label="File", menu=f_menu)
        self._on_request_new_rhythm_window = no_callback
        self._on_show_settings_window_request = no_callback
        self._on_show_midi_batch_export_window_request = no_callback
        self._on_request_exit = no_callback

    @property
    def on_request_new_rhythm_window(self) -> tp.Callable:
        return self._on_request_new_rhythm_window

    @on_request_new_rhythm_window.setter
    def on_request_new_rhythm_window(self, callback: tp.Callable):
        if not callable(callback):
            raise TypeError("Expected callable but got \"%s\"" % str(callback))
        self._on_request_new_rhythm_window = callback

    @property
    def on_request_exit(self):
        return self._on_request_exit

    @on_request_exit.setter
    def on_request_exit(self, callback: tp.Callable):
        if not callable(callback):
            raise TypeError("Expected callable but got \"%s\"" % str(callback))
        self._on_request_exit = callback

    @property
    def on_request_show_settings_window(self):
        return self._on_show_settings_window_request

    @on_request_show_settings_window.setter
    def on_request_show_settings_window(self, callback: tp.Callable):
        if not callable(callback):
            raise TypeError("Expected callable but got \"%s\"" % str(callback))
        self._on_show_settings_window_request = callback

    @property
    def on_request_show_midi_batch_export_window(self) -> tp.Callable:
        return self._on_show_midi_batch_export_window_request

    @on_request_show_midi_batch_export_window.setter
    def on_request_show_midi_batch_export_window(self, callback: tp.Callable):
        if not callable(callback):
            raise TypeError("Expected callable but got \"%s\"" % str(callback))
        self._on_show_midi_batch_export_window_request = callback


class InvalidInput(Exception):
    def __init__(self, *args, show: bool = True, **kw):
        super().__init__(*args, **kw)
        self.show = bool(show)


class BSConfigInput(object, metaclass=ABCMeta):
    """Abstract base class for input widgets that need to sync with a :class:`beatsearch.app.config.BSConfig` entry"""

    def __init__(self, master: tk.BaseWidget, config: BSConfig):
        self.master = master             # type: tk.BaseWidget
        self.config = config             # type: BSConfig
        self._on_change_callback = None  # type: tp.Callable

    @classmethod
    @abstractmethod
    def get_name(cls) -> str:
        """Returns the name of this input used for this input's label

        :return: name of this input as a string
        """

        raise NotImplementedError

    @abstractmethod
    def get_widget(self) -> tk.BaseWidget:
        """Returns the tkinter widget of this input. This may be a container widget.

        :return: tkinter widget
        """

        raise NotImplementedError

    @abstractmethod
    def reset(self) -> None:
        """Resets the value of this input based on self

        :return: None
        """

        raise NotImplementedError

    @abstractmethod
    def check_input(self, raw_value: tp.Any):
        """Checks the given raw input value

        :param raw_value: result of calling :meth:`tkinter.Variable.get` on the variable returned
                          by :meth:`beatsearch.BSConfigInput.__get_variable__`

        :return:          None

        :raises :class:`beatsearch.app.InvalidInput` if raw input is not valid
        """

        raise NotImplementedError

    @abstractmethod
    def __get_variable__(self) -> tk.Variable:
        """Returns the tkinter variable linked to the input

        :return: tkinter variable controlled by this input
        """

        raise NotImplementedError

    def get_value(self) -> tp.Any:
        """Returns the current value of this input

        Override this method in case that the input value should be mapped to something else. For example, labels of
        dropdown menus might need to be mapped to an internal value.

        :return: value of input
        """

        return self.__get_variable__().get()

    def set_value(self, value: tp.Any) -> None:
        """Sets the value of this input

        Override this method in case that the input value should be mapped to something else. For example, labels of
        dropdown menus might need to be mapped to an internal value.

        :param value: value of input
        :return: None
        """

        self.__get_variable__().set(value)

    def on_change(self, callback: tp.Callable) -> None:
        """Sets the on_change callback for this input. Only one callback is allowed.

        :param callback: callable or None to remove the callback
        :return: None
        """

        variable = self.__get_variable__()
        old_callback = self._on_change_callback

        if old_callback:
            variable.trace_remove("w", old_callback)

        if not callback:
            self._on_change_callback = None
            return

        variable.trace_add("write", callback)


class ComboboxInput(BSConfigInput):
    """Input consisting of a combobox which will reflect the labels of the internal values"""

    def __init__(self, master: tk.BaseWidget, config: BSConfig):
        super().__init__(master, config)
        self._var_str = tk.StringVar()
        labels = tuple(self.get_labels_by_values().values())
        self._combobox = ttk.Combobox(self.master, values=labels, textvariable=self._var_str, state="readonly")

    @classmethod
    @abstractmethod
    def get_values_by_labels(cls) -> OrderedDict:
        """Returns a dictionary containing the values by their combobox labels

        :return: ordered dictionary containing values (dict values) by labels (dict keys)
        """

        raise NotImplementedError

    @classmethod
    def get_labels_by_values(cls) -> OrderedDict:
        """Returns a dictionary containing the combobox labels by their underlying values

        :return: ordered dictionary containing labels (dict values) by values (dict keys)
        """

        values_by_labels = cls.get_values_by_labels()
        return OrderedDict((value, label) for (label, value) in values_by_labels.items())

    def get_widget(self) -> tk.BaseWidget:
        return self._combobox

    def get_value(self) -> tp.Any:
        # returns underlying value of current combobox label
        label = super().get_value()
        return self.get_values_by_labels()[label]

    def set_value(self, value: tp.Any) -> None:
        # sets underlying value and updates combobox by its corresponding label
        label = self.get_labels_by_values()[value]
        super().set_value(label)

    def check_input(self, raw_value: tp.Any):
        try:
            self.get_value()
        except KeyError:
            labels = self.get_labels_by_values().values()
            raise InvalidInput(textwrap.dedent("""
                Unknown value: %s
                Choose between: %s
            """) % (raw_value, str(labels)))

    def __get_variable__(self) -> tk.Variable:
        return self._var_str


class DirectoryInput(BSConfigInput, metaclass=ABCMeta):
    """Input consisting of a text entry and a 'Browse' button, which will open a browse directory dialog"""

    def __init__(
            self,
            master: tk.BaseWidget, config: BSConfig,
            allow_empty_dir: bool,
            ask_create_dir_if_not_exist: bool
    ):
        super().__init__(master, config)

        self._var_directory = tk.StringVar()
        self._container = tk.Frame(self.master)
        self._ask_create_dir = ask_create_dir_if_not_exist
        self._allow_empty_dir = allow_empty_dir

        tk.Entry(self._container, textvariable=self._var_directory).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(self._container, text="Browse", command=self._on_btn_browse, width=12) \
            .pack(side=tk.RIGHT, padx=(3, 0))

    def get_widget(self) -> tk.BaseWidget:
        return self._container

    def __get_variable__(self) -> tk.Variable:
        return self._var_directory

    def get_value(self) -> tp.Any:
        directory = super().get_value()
        return normalize_directory(directory)

    def set_value(self, directory: tp.Any) -> None:
        directory = normalize_directory(directory)
        super().set_value(directory)

    def check_input(self, raw_value: tp.Any):
        if not raw_value and not self._allow_empty_dir:
            raise InvalidInput("No directory given")

        directory = self.get_value()
        parent_dir = os.path.dirname(directory)

        if not os.path.isdir(directory):
            if self._ask_create_dir:
                if not os.path.isdir(parent_dir):
                    raise InvalidInput(textwrap.dedent("""
                        No such directory: %s
                        
                        If its parent directory had existed, I'd have asked you 
                        to create a new one, but it doesn't.
                    """) % raw_value)

                create_new_dir = messagebox.askyesno("Browse directory", textwrap.dedent("""
                    No such directory: %s
                    
                    Do you want to create it?
                """ % raw_value))

                if create_new_dir:
                    os.mkdir(directory)
                else:
                    # just break the validation without showing another error message
                    raise InvalidInput(silent=True)
            else:
                raise InvalidInput("No such directory: %s" % raw_value)

    def _on_btn_browse(self):
        current_root_dir = self._var_directory.get()

        # NOTE: askdirectory returns the path with forward slashes, even on Windows!
        directory = tkinter.filedialog.askdirectory(
            title=self.get_name(),
            parent=self.master,
            initialdir=current_root_dir
        )

        if not os.path.isdir(directory):
            return

        self._var_directory.set(directory)


class IntegerInput(BSConfigInput, metaclass=ABCMeta):
    """Input consisting of a text entry with an integer value"""

    def __init__(self, master: tk.BaseWidget, config: BSConfig):
        super().__init__(master, config)
        self._var_str = tk.StringVar()
        self._entry = tk.Entry(self.master, textvariable=self._var_str)

    def get_widget(self) -> tk.Widget:
        return self._entry

    def get_value(self) -> tp.Any:  # returns the value as an integer
        str_value = super().get_value()
        return int(str_value)

    def set_value(self, value: tp.Any) -> None:  # sets the value as an integer
        str_value = str(value)
        super().set_value(str_value)

    def check_input(self, raw_value: str):
        if not raw_value:
            raise InvalidInput("Man. I can't magically deduce an integer from nothing.")

        try:
            self.get_value()
        except ValueError:
            raise InvalidInput("How is '%s' a number..? ;-)" % raw_value)

    def __get_variable__(self) -> tk.Variable:
        return self._var_str


class BSConfigFormMixin(object, metaclass=ABCMeta):
    """This mixin adds functionality to pack and validate BSConfigInput widgets"""

    def __init__(self):
        config = self.get_config()
        master = self.get_master_widget()

        self.form_container = tk.Frame(master)
        self._inputs = dict()

        for input_cls in self.get_input_classes():
            # frame containing the input and its label
            input_container = tk.Frame(self.form_container)

            # we instantiate the BSConfigInput and get its widget
            input_obj = input_cls(input_container, config)  # type: BSConfigInput
            input_widget = input_obj.get_widget()           # type: tk.Widget
            label_widget = tk.Label(input_container, text=input_obj.get_name(), anchor=tk.W)  # input label widget

            # set input to correct initial value
            input_obj.reset()

            # pack everything up
            label_widget.pack(fill=tk.X)
            input_widget.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
            input_container.pack(fill=tk.X)

            # keep track of the input for validation
            assert input_cls not in self._inputs, "duplicate BSConfigInput class: %s" % input_cls
            self._inputs[input_cls] = input_obj

    @classmethod
    @abstractmethod
    def get_input_classes(cls) -> tp.Iterable[tp.Type[BSConfigInput]]:
        """Returns all input classes for the inputs in this form"""

        raise NotImplementedError

    @abstractmethod
    def get_config(self) -> BSConfig:
        """Returns the configuration object"""

        raise NotImplementedError

    def get_master_widget(self) -> tk.BaseWidget:
        """Returns the form master tkinter widget

        Override this is mixin is not used on a tkinter base widget.
        """

        assert isinstance(self, tk.BaseWidget)
        return self

    def get_input_iterator(self) -> tp.Iterator[BSConfigInput]:
        """Returns an iterator over the inputs in this form

        :return: iterator yielding the inputs in this form
        """

        return iter(self._inputs.values())

    def reset_all_inputs(self) -> None:
        """Resets the inputs of this form

        :return: None
        """

        for inp in self._inputs.values():
            inp.reset()

    def get_input(self, input_cls: tp.Type[BSConfigInput]) -> BSConfigInput:
        """Returns the input object of the given input type

        :param input_cls: input type as a :class:`beatsearch.app.view.BSConfigInput` subclass
        :return: input of given input type
        :raises: ValueError if this form contains no input of the given type
        """

        try:
            return self._inputs[input_cls]
        except KeyError:
            raise ValueError("No input with class: %s (you might want to add it to get_input_classes)" % str(input_cls))

    def validate_inputs(self) -> bool:
        """Validates this form and shows an error message dialog in case of an invalid input

        :return: True if all inputs are valid; False otherwise
        """

        master = self.get_master_widget()

        for inp in self._inputs.values():
            try:
                raw_value = inp.__get_variable__().get()
                inp.check_input(raw_value)
            except InvalidInput as e:
                if e.show:
                    messagebox.showerror(
                        parent=master,
                        title=inp.get_name(),
                        message=str(e)
                    )
                return False
        return True


class BSSettingsWindow(BSAppWindow, BSConfigFormMixin):
    TITLE = "Settings"

    class RhythmsRootDirInput(DirectoryInput):
        NAME = "Rhythms root directory"

        def __init__(self, master: tk.BaseWidget, config: BSConfig):
            super().__init__(master, config, allow_empty_dir=True, ask_create_dir_if_not_exist=False)

        @classmethod
        def get_name(cls) -> str:
            return cls.NAME

        def reset(self) -> None:
            root_dir = self.config.midi_root_directory.get()
            if root_dir:
                assert os.path.isdir(root_dir), "directory doesn't exist: %s" % root_dir  # TODO handle this properly
            self.__get_variable__().set(root_dir)

    class RhythmResolutionInput(IntegerInput):
        NAME = "Rhythm resolution (PPQN)"

        @classmethod
        def get_name(cls) -> str:
            return cls.NAME

        def check_input(self, raw_value: str):
            super().check_input(raw_value)
            value = self.get_value()  # shouldn't raise any exceptions, since super().check_input didn't
            if value <= 0:
                raise InvalidInput("Resolution must be greater than zero")

        def reset(self) -> None:
            resolution = self.config.rhythm_resolution.get()
            self.set_value(resolution)

    class MidiDrumMappingReducerInput(ComboboxInput):
        NAME = "Midi Mapping Reducer"
        MAPPING_REDUCER_FRIENDLY_NAMES = tuple(get_drum_mapping_reducer_implementation_friendly_names())

        @classmethod
        def get_values_by_labels(cls) -> OrderedDict:
            reducers_by_friendly_names = OrderedDict()
            reducers_by_friendly_names['None'] = None

            for friendly_name in cls.MAPPING_REDUCER_FRIENDLY_NAMES:
                reducers_by_friendly_names[friendly_name] = get_drum_mapping_reducer_implementation(friendly_name)

            return reducers_by_friendly_names

        @classmethod
        def get_name(cls) -> str:
            return cls.NAME

        def reset(self) -> None:
            reducer = self.config.mapping_reducer.get()
            self.set_value(reducer)

    def __init__(self, app, min_width=360, min_height=60, **kwargs):
        BSAppWindow.__init__(self, app, **kwargs)
        BSConfigFormMixin.__init__(self)

        self.wm_title(self.TITLE)
        self.minsize(min_width, min_height)
        self.resizable(False, False)

        self.btn_container = tk.Frame(self)
        self.form_container.pack(fill=tk.BOTH, padx=6, pady=6)

        # we keep track of the initial input values to check whether settings where changed by the user
        # since the settings window was opened
        self._initial_input_values = dict()  # type: tp.Dict[BSConfigInput, tp.Any]

        for inp in self.get_input_iterator():
            self._initial_input_values[inp] = inp.get_value()
            inp.on_change(eat_args(self._update_btn_apply_state))

        self._bottom_btn_bar = tk.Frame(self)
        self._btn_apply = self._setup_button_bar()

    @classmethod
    def get_input_classes(cls) -> tp.Iterable[tp.Type[BSConfigInput]]:
        return [
            cls.RhythmsRootDirInput,
            cls.RhythmResolutionInput,
            cls.MidiDrumMappingReducerInput
        ]

    def get_config(self) -> BSConfig:
        return self.controller.get_config()

    def _handle_ok(self):
        if self._check_if_settings_changed() and not self._handle_apply():
            # don't close the settings window if something
            # _handle_apply did not go well (e.g. validation err)
            return
        self.destroy()

    def _handle_apply(self):
        controller = self.controller
        config = controller.get_config()

        if not self.validate_inputs():
            return False

        rhythms_root_dir = self.get_input(self.RhythmsRootDirInput).get_value()
        rhythm_resolution = self.get_input(self.RhythmResolutionInput).get_value()
        mapping_reducer = self.get_input(self.MidiDrumMappingReducerInput).get_value()

        # update and save config .ini file
        config.rhythm_resolution.set(rhythm_resolution)
        config.midi_root_directory.set(rhythms_root_dir)
        config.mapping_reducer.set(mapping_reducer)
        config.save()

        # reload the corpus with the new settings
        controller.load_corpus()

        # reset the initial values (now that they're saved)
        self._reset_initial_values()
        self._update_btn_apply_state()
        return True

    def _check_if_settings_changed(self):
        for inp in self.get_input_iterator():
            initial_value = self._initial_input_values[inp]
            curr_value = inp.get_value()
            if initial_value != curr_value:
                return True
        return False

    def _update_btn_apply_state(self):
        self._btn_apply.config(state=tk.NORMAL if self._check_if_settings_changed() else tk.DISABLED)

    def _reset_initial_values(self):
        self._initial_input_values.clear()
        for inp in self.get_input_iterator():
            self._initial_input_values[inp] = inp.get_value()

    def _setup_button_bar(self):  # returns the apply button (whoever this is reading, sorry, this is not intuitive)
        btn_bar = self._bottom_btn_bar

        btn_ok = tk.Button(btn_bar, text="OK", command=self._handle_ok)
        btn_cancel = tk.Button(btn_bar, text="Cancel", command=self.destroy)
        btn_apply = tk.Button(btn_bar, text="Apply", command=self._handle_apply, state=tk.DISABLED)

        buttons = (btn_ok, btn_cancel, btn_apply)
        largest_btn_text = max(len(btn.cget("text")) for btn in buttons)

        for btn in reversed(buttons):
            btn.configure(width=largest_btn_text)
            btn.pack(side=tk.RIGHT, padx=(0, 3))

        btn_bar.pack(side=tk.BOTTOM, fill=tk.X, pady=6, padx=3)
        return btn_apply


class BSMidiBatchExportWindow(BSAppWindow, BSConfigFormMixin):
    TITLE = "Midi Batch Export"

    class MidiExportDirectory(DirectoryInput):
        NAME = "MIDI Export Directory"

        def __init__(self, master: tk.BaseWidget, config: BSConfig):
            super().__init__(master, config, allow_empty_dir=False, ask_create_dir_if_not_exist=True)

        @classmethod
        def get_name(cls) -> str:
            return cls.NAME

        def reset(self) -> None:
            export_dir = self.config.midi_batch_export_directory.get()
            self.set_value(export_dir)

    class RhythmsToExport(ComboboxInput):
        NAME = "Rhythms to export"

        @classmethod
        def get_values_by_labels(cls) -> OrderedDict:
            d = OrderedDict()
            d['Selected rhythms'] = "selection"
            d['All rhythms'] = "all"
            return d

        @classmethod
        def get_name(cls) -> str:
            return cls.NAME

        def reset(self) -> None:
            rhythms_to_export = self.config.midi_batch_export_rhythms_to_export.get()
            self.set_value(rhythms_to_export)

    def __init__(self, app, min_width=360, min_height=60, **kwargs):
        BSAppWindow.__init__(self, app, **kwargs)
        BSConfigFormMixin.__init__(self)

        self.wm_title(self.TITLE)
        self.minsize(min_width, min_height)
        self.resizable(False, False)

        self.form_container.pack(fill=tk.BOTH, padx=6, pady=6)
        self._button_bar = tk.Frame(self)
        self._setup_button_bar()

    @classmethod
    def get_input_classes(cls) -> tp.Iterable[tp.Type[BSConfigInput]]:
        return [
            cls.MidiExportDirectory,
            cls.RhythmsToExport
        ]

    def get_config(self) -> BSConfig:
        return self.controller.get_config()

    def _handle_ok(self):
        controller = self.controller
        config = controller.get_config()

        if not self.validate_inputs():
            return

        export_dir = self.get_input(self.MidiExportDirectory).get_value()
        rhythms_to_export = self.get_input(self.RhythmsToExport).get_value()

        # export the rhythms
        controller.export_rhythms_as_midi(export_dir, rhythms_to_export)

        # update the "default" midi batch export directory every time that a midi batch export is performed
        config.midi_batch_export_directory.set(export_dir)
        config.save()

        self.destroy()

    def _setup_button_bar(self):
        btn_bar = self._button_bar
        btn_export = tk.Button(btn_bar, text="Export", command=self._handle_ok, width=15)
        btn_cancel = tk.Button(btn_bar, text="Cancel", command=self.destroy)
        btn_width = max(len(btn.cget("text")) for btn in (btn_export, btn_cancel))

        for btn in reversed([btn_export, btn_cancel]):
            btn.configure(width=btn_width)
            btn.pack(side=tk.RIGHT, padx=(0, 3))

        btn_bar.pack(side=tk.BOTTOM, expand=True, fill=tk.X, padx=4, pady=4)


class BSApp(tk.Tk, object):
    WINDOW_TITLE = "BeatSearch"

    STYLES = {
        'inner-pad-x': 0,
        'inner-pad-y': 6.0
    }

    FRAME_RHYTHM_LIST = "<Frame-RhythmList>"
    FRAME_TRANSPORT = "<Frame-Transport>"
    FRAME_SEARCH = "<Frame-Search>"
    FRAME_RHYTHM_COMPARISON_STRIP = "<Frame-RhythmComparisonStrip>"

    FONT = {
        'header': ("Helvetica", 14),
        'normal': ("Helvetica", 12)
    }

    def __init__(
            self,
            controller: BSController = BSController(),  # TODO Get rid of mutable default
            search_frame_cls: tp.Type[BSSearchForm] = BSSearchForm,
            rhythms_frame_cls: tp.Type[BSRhythmList] = BSRhythmList,
            transport_frame_cls: tp.Union[tp.Type[BSTransportControls], None] = BSTransportControls,
            rhythm_comparison_strip_frame_cls: tp.Type[BSRhythmComparisonStrip] = BSRhythmComparisonStrip,
            main_menu: tp.Union[BSMainMenu, tp.Type[BSMainMenu], None] = BSMainMenu,
            background="#EEEEEE",
            **kwargs
    ):
        tk.Tk.__init__(self, **kwargs)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Control-c>", eat_args(self.close))
        signal.signal(signal.SIGTERM, eat_args(self.close))
        signal.signal(signal.SIGINT, eat_args(self.close))
        self.is_closed = True

        self.wm_title(BSApp.WINDOW_TITLE)
        self.config(bg=background)
        self._midi_rhythm_loader_by_dialog = BSMidiFileRhythmLoader()
        self.controller = self._controller = controller
        self._menubar = type_check_and_instantiate_if_necessary(main_menu, BSMainMenu, allow_none=True, root=self)
        self.frames = OrderedDict()

        # frame name, frame class, instantiation args, pack args
        frame_info = (
            (
                BSApp.FRAME_SEARCH,
                search_frame_cls,
                dict(background=background),
                dict(expand=False, fill=tk.X, pady=3)
            ),

            (
                BSApp.FRAME_RHYTHM_LIST,
                rhythms_frame_cls,
                dict(background=color_variant(background, 1)),
                dict(expand=True, fill=tk.BOTH, pady=(3, 0))
            ),

            (
                BSApp.FRAME_RHYTHM_COMPARISON_STRIP,
                rhythm_comparison_strip_frame_cls,
                dict(background_left=color_variant(background, -0.055), background_right=background),
                dict(expand=False, fill=tk.X, pady=0),
            ),

            (
                BSApp.FRAME_TRANSPORT,
                transport_frame_cls,
                dict(),
                dict(expand=False, fill=tk.X, pady=(3, 0))
            ),
        )

        padx = BSApp.STYLES['inner-pad-x']

        for frame_name, frame_cls, frame_args, pack_args in frame_info:
            if frame_cls is None:
                continue
            frame = frame_cls(self, **frame_args)
            self.frames[frame_name] = frame
            frame.pack(**{
                'side': tk.TOP,
                'padx': padx,
                **pack_args
            })

        self._setup_menubar()
        self._setup_frames()

        redraw_frames_on_controller_callbacks = {
            BSController.RHYTHM_SELECTION: [BSApp.FRAME_TRANSPORT, BSApp.FRAME_RHYTHM_COMPARISON_STRIP],
            BSController.RHYTHM_PLAYBACK_START: [BSApp.FRAME_TRANSPORT],
            BSController.RHYTHM_PLAYBACK_STOP: [BSApp.FRAME_TRANSPORT],
            BSController.CORPUS_LOADED: [BSApp.FRAME_RHYTHM_LIST, BSApp.FRAME_RHYTHM_COMPARISON_STRIP],
            BSController.DISTANCES_TO_TARGET_UPDATED: [BSApp.FRAME_RHYTHM_LIST],
            BSController.TARGET_RHYTHM_SET: [BSApp.FRAME_SEARCH, BSApp.FRAME_RHYTHM_COMPARISON_STRIP],
            BSController.DISTANCE_MEASURE_SET: [BSApp.FRAME_SEARCH]
        }

        for action, frames in redraw_frames_on_controller_callbacks.items():
            def get_callback(_frames):
                def callback(*_, **__):
                    self.redraw_frames(*_frames)
                return callback
            self.controller.bind(action, get_callback(frames))

        # set loading error handler on new rhythm loaders
        self.controller.bind(
            BSController.RHYTHM_LOADER_REGISTERED,
            lambda loader: setattr(loader, "on_loading_error", self._on_loading_error)
        )

        # keyboard shortcuts
        self.bind_all("<Control-,>", eat_args(self.show_settings_window))
        self.bind_all("<Control-Shift-E>", eat_args(self.show_midi_batch_export_window))

        self.redraw_frames()

    @property
    def controller(self):  # type: () -> tp.Union[BSController, None]
        return self._controller

    @controller.setter
    def controller(self, controller: tp.Union[BSController, None]):
        self.unbind_all("<space>")

        # reset rhythm loading error handlers
        for _, loader in controller.get_rhythm_loader_iterator():
            if loader.on_loading_error == self._on_loading_error:
                loader.on_loading_error = no_callback

        if controller is None:
            self._controller = None
            return

        if not isinstance(controller, BSController):
            raise TypeError("Expected a BSController but got \"%s\"" % str(controller))

        if controller.is_rhythm_player_set():  # bind space for whole application
            self.bind_all("<space>", eat_args(self._toggle_rhythm_playback))

        # update window title on new corpus loaded
        controller.bind(BSController.CORPUS_LOADED, self.update_window_title)

        # add rhythm loader from MIDI file with dialog
        controller.register_rhythm_loader(self._midi_rhythm_loader_by_dialog)

        # set rhythm loading error handlers
        for _, loader in controller.get_rhythm_loader_iterator():
            loader.on_loading_error = self._on_loading_error

        self._controller = controller

        # force-update the window title as we might have missed the CORPUS_LOADED event if the corpus has been loaded
        # before the controller is set
        self.update_window_title()

    def redraw_frames(self, *frame_names):
        if not frame_names:
            frame_names = self.frames.keys()
        for name in frame_names:
            try:
                self.frames[name].redraw()
            except KeyError:
                pass

    def get_frame_names(self):
        return list(self.frames.keys())

    def mainloop(self, n=0):
        self.is_closed = False
        super().mainloop(n)

    def close(self):
        self.quit()
        self.is_closed = True

    def update_window_title(self):
        corpus_fname = self.controller.get_corpus_rootdir_name() or "<No rhythms directory set>"
        title = "%s - %s" % (corpus_fname, self.WINDOW_TITLE)
        self.wm_title(title)

    def show_settings_window(self):
        settings_window = BSSettingsWindow(self)
        settings_window.focus()

    def show_midi_batch_export_window(self):
        midi_export_window = BSMidiBatchExportWindow(self)
        midi_export_window.focus()

    @staticmethod
    def show_save_midi_box(fname: str) -> str:
        fpath = filedialog.asksaveasfilename(
            title="Export as MIDI file",
            defaultextension=".mid",
            initialfile=fname,
            filetypes=(("MIDI files", "*.mid"), ("All files", "*.*"))
        )

        return fpath

    def _setup_frames(self):
        search_frame = self.frames[BSApp.FRAME_SEARCH]
        rhythms_frame = self.frames[BSApp.FRAME_RHYTHM_LIST]  # type: BSRhythmList
        rhythms_frame.on_request_target_rhythm = self._handle_target_rhythm_request
        rhythms_frame.on_request_export_rhythm_as_midi = self._handle_export_rhythm_as_midi_request
        search_frame.search_command = self.controller.calculate_distances_to_target_rhythm
        search_frame.on_new_measure = self.controller.set_distance_measure
        search_frame.on_new_tracks = self.controller.set_tracks_to_compare

        def on_new_quantize_unit(unit_title, controller):  # type: (str, BSController) -> None
            unit = UNITS_BY_UNIT_TITLES[unit_title]
            controller.set_measure_quantization_unit(unit)

        search_frame.on_new_quantize = partial(on_new_quantize_unit, controller=self.controller)

        try:
            transport_frame = self.frames[BSApp.FRAME_TRANSPORT]
            transport_frame.toggle_play_command = self._toggle_rhythm_playback
        except KeyError:
            pass

    def _setup_menubar(self):
        menubar = self._menubar
        if menubar is None:
            return
        menubar.on_request_show_settings_window = self.show_settings_window
        menubar.on_request_show_midi_batch_export_window = self.show_midi_batch_export_window
        menubar.on_request_exit = self.close
        self.config(menu=menubar)

    def _toggle_rhythm_playback(self):
        controller = self.controller
        if controller.is_rhythm_player_playing():
            controller.stop_rhythm_playback()
        else:
            controller.playback_selected_rhythms()

    def _handle_target_rhythm_request(self, rhythm_ix):
        controller = self.controller
        rhythm = controller.get_rhythm_by_index(rhythm_ix)
        controller.set_target_rhythm(rhythm)

    def _handle_export_rhythm_as_midi_request(self, rhythm_ix: int, fname: str):
        controller = self.controller
        fpath = self.show_save_midi_box(fname)
        controller.export_single_rhythm_as_midi(fpath, rhythm_ix)

    @staticmethod
    def _on_loading_error(loading_error: BSMidiRhythmLoader.LoadingError):
        messagebox.showerror(
            title="Rhythm loading error",
            message=str(loading_error)
        )


class ToggleButton(tk.Button):
    def __init__(
            self,
            master,
            text,
            background=(None, "#009688"),
            foreground=(None, None),
            **kwargs
    ):
        tk.Button.__init__(self, master, **kwargs)

        if not (0 < len(text) <= 2):
            raise ValueError("Expected a tuple or list containing exactly 1 or 2 elements but got '%s'" % str(text))

        # set false colors to default
        background = [self.cget("background") if not c else c for c in background]
        foreground = [self.cget("foreground") if not c else c for c in foreground]

        self._toggled = False
        self._background = background
        self._foreground = foreground
        self._text = text
        self._enabled = True
        self.set_toggle(False)

    def set_toggle(self, toggle=True):
        self._toggled = bool(toggle)
        self.redraw()

    def redraw(self):
        i = int(self._toggled)
        self.config(
            background=self._background[i],
            fg=self._foreground[i],
            text=self._text[i],
            state=tk.NORMAL if self._enabled else tk.DISABLED
        )

    def toggle(self):
        self.set_toggle(not self._toggled)

    def set_enabled(self, enabled=True):
        self._enabled = bool(enabled)
        self.redraw()


class HorizontalScrolledFrame(tk.Frame, object):
    """https://stackoverflow.com/a/16198198/5508855"""

    def __init__(self, parent, *args, **kw):
        tk.Frame.__init__(self, parent, *args, **kw)

        # create a canvas object and a vertical scrollbar for scrolling it
        scrollbar = tk.Scrollbar(self, orient=tk.HORIZONTAL)
        scrollbar.pack(fill=tk.X, side=tk.BOTTOM, expand=tk.FALSE)
        canvas = tk.Canvas(self, bd=0, highlightthickness=0, xscrollcommand=scrollbar.set, bg=self.cget("bg"))
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=tk.TRUE)
        scrollbar.config(command=canvas.xview)
        self._canvas = canvas

        # reset the view
        canvas.xview_moveto(0)
        canvas.yview_moveto(0)

        # create a frame inside the canvas which will be scrolled with it
        self.interior = interior = tk.Frame(canvas)
        canvas.create_window(0, 0, window=interior, anchor=tk.NW)

        # track changes to the canvas and frame height and sync them, also updating the scrollbar
        def configure_interior(_):
            # update the scrollbars to match the size of the inner frame
            size = (interior.winfo_reqwidth(), interior.winfo_reqheight())
            canvas.config(scrollregion="0 0 %s %s" % size)
            if interior.winfo_reqheight() != canvas.winfo_height():
                # update the canvas's height to fit the inner frame
                canvas.config(height=interior.winfo_reqheight())

        interior.bind("<Configure>", configure_interior)

    def xview_scroll(self, number, what):
        canvas = self._canvas
        interior_width = self.interior.winfo_width()
        canvas_width = canvas.winfo_width()
        # reset if interior is smaller than canvas
        if interior_width < canvas_width:
            canvas.xview_moveto(0)
            return
        canvas.xview_scroll(number, what)
