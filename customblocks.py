
import crappy
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.widgets
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from typing import NoReturn, Optional, Tuple
# from matplotlib.widgets import Button
# import SoftC10TL27_avec_modif


class YBlock(crappy.blocks.Block):

   def __init__(self, cmd_labels = None, out_labels = None, freq = 50):
      super().__init__()
      self.cmd_labels = cmd_labels if cmd_labels is not None else out_labels
      self.out_labels = out_labels
      self.freq = freq
   
   def prepare(self):
      self.output = {}

   def loop(self):
      for link in self.inputs:
         recv_dict = link.recv_last()
         if recv_dict is not None:
            for label in recv_dict:
               self.output[label] = recv_dict[label]
      
      # if self.output == {} :
      #    self.output["sortie_charge_transformee"] = 0.0
      #    self.output["consigne"] = 0.0
      #    self.output["t(s)"] = 0.0
      self.send(self.output)

class EmbeddedGrapher(crappy.blocks.Block):
   ### Copie quasi-conforme du bloc Grapher de CRAPPy, mais intégrable à une fenêtre. Ou pas.
   """The grapher receive data from a block and plots it.

   Multiple curves can be plotted on a same graph, and the data can come from
   different blocks.

   Note:
      To reduce the memory and CPU usage of graphs, try lowering the ``maxpt``
      parameter (2-3000 is already enough to follow a short test), or set the
      ``length`` parameter to a non-zero value (again, 2-3000 is fine). Lowering
      the ``freq`` is also a good option to limit the CPU use.
   """

   def __init__(self,
               *labels: Tuple[str, str],
               length: int = 0,
               freq: float = 2,
               maxpt: int = 20000,
               window_size: Tuple[int, int] = (8, 8),
               window_pos: Optional[Tuple[int, int]] = None,
               interp: bool = True,
               backend: str = "TkAgg",
               verbose: bool = False
               ) -> None:
      """Sets the args and initializes the parent class.

      Args:
         *labels (:obj:`tuple`): Each :obj:`tuple` corresponds to a curve to plot,
            and should contain two values: the first will be the label of the `x`
            values, the second the label of the `y` values. There's no limit to the
            number of curves. Note that all the curves are displayed in a same
            graph.
         length (:obj:`int`, optional): If `0` the graph is static and displays
            all data from the start of the assay. Else only displays the last
            ``length`` received chunks, and drops the previous ones.
         freq (:obj:`float`, optional): The refresh rate of the graph. May cause
            high CPU use if set too high.
         maxpt (:obj:`int`, optional): The maximum number of points displayed on
            the graph. When reaching this limit, the block deletes one point out of
            two to avoid using too much memory and CPU.
         window_size (:obj:`tuple`, optional): The size of the graph, in inches.
         window_pos (:obj:`tuple`, optional): The position of the graph in pixels.
            The first value is for the `x` direction, the second for the `y`
            direction. The origin is the top-left corner. Works with multiple
            screens.
         interp (:obj:`bool`, optional): If :obj:`True`, the data points are
            linked together by straight lines. Else, only the points are displayed.
         backend (:obj:`int`, optional): The :mod:`matplotlib` backend to use.
            Performance may vary according to the chosen backend. Also, every
            backend may not be available depending on your machine.
         verbose (:obj:`bool`, optional): To display the loop frequency of the
            block.

      Example:
         ::

            graph = Grapher(('t(s)', 'F(N)'), ('t(s)', 'def(%)'))

         will plot a dynamic graph with two lines plot (`F=f(t)` and `def=f(t)`).
         ::

            graph = Grapher(('def(%)', 'F(N)'), length=0)

         will plot a static graph.
         ::

            graph = Grapher(('t(s)', 'F(N)'), length=30)

         will plot a dynamic graph displaying the last 30 chunks of data.
      """

      crappy.blocks.Block.__init__(self)
      self.niceness = 10
      self._length = length
      self.freq = freq
      self._maxpt = maxpt
      self._window_size = window_size
      self._window_pos = window_pos
      self._interp = interp
      self._backend = backend
      self.verbose = verbose
      self._labels = labels

   def prepare(self) -> None:

      # Switch to the required backend
      if self._backend:
        plt.switch_backend(self._backend)

      # Create the figure and the subplot
      self._figure = plt.figure(figsize=self._window_size)
      self._canvas = self._figure.canvas
      self._ax = self._figure.add_subplot(111)

      # Add the lines or the dots
      self._lines = []
      for _ in self._labels:
         if self._interp:
            self._lines.append(self._ax.plot([], [])[0])
         else:
            self._lines.append(self._ax.plot([], [], 'o', markersize=3)[0])

      # Keep only 1/factor points on each line
      self._factor = [1 for _ in self._labels]
      # Count to drop exactly 1/factor points, no more and no less
      self._counter = [0 for _ in self._labels]

      # Add the legend
      legend = [y for x, y in self._labels]
      plt.legend(legend, bbox_to_anchor=(-0.03, 1.02, 1.06, .102), loc=3,
                  ncol=len(legend), mode="expand", borderaxespad=1)
      plt.xlabel(self._labels[0][0])
      plt.ylabel(self._labels[0][1])

      # Add a grid
      plt.grid()

      ## Ajout
      # Gère l'arrêt de >crappy
      self._stop_button = matplotlib.widgets.Button(plt.axes([.83, .02, .12, .05]), 'Stop')
      self._stop_button.on_clicked(lambda e : self.finish())
      # self._canvas.mpl_connect("close_event", self._stopping_crappy)

      # Adds a button for clearing the graph
      self._clear_button = matplotlib.widgets.Button(plt.axes([.7, .02, .12, .05]), 'Effacer')
      self._clear_button.on_clicked(self._clear)

      # Set the dimensions if required
      if self._window_pos:
         mng = plt.get_current_fig_manager()
         mng.window.wm_geometry("+%s+%s" % self._window_pos)

      # Ready to show the window
      plt.show(block=False)
      plt.pause(.001)

   def loop(self) -> None:

      # Receives the data sent by the upstream blocks
      if self.freq >= 10:
         # Assuming that above 10Hz the data won't saturate the links
         data = self.recv_all_delay()
      else:
         # Below 10Hz, making sure to flush the pipes at least every 0.1s
         data = self.recv_all_delay(delay=1 / 2 / self.freq,
                                    poll_delay=min(0.1, 1 / 2 / self.freq))

      update = False  # Should the graph be updated ?

      # For each curve, looking for the corresponding labels in the received data
      for i, (lx, ly) in enumerate(self._labels):
         x, y = None, None
         for dict_ in data:
            if lx in dict_ and ly in dict_:
               # Found the corresponding data, getting the new data according to the
               # current resampling factors
               dx = dict_[lx][self._factor[i] - self._counter[i] -
                              1::self._factor[i]]
               dy = dict_[ly][self._factor[i] - self._counter[i] -
                              1::self._factor[i]]
               # Recalculating the counter
               self._counter[i] = (self._counter[i] +
                                    len(dict_[lx])) % self._factor[i]
               # Adding the new points to the arrays
               x = np.hstack((self._lines[i].get_xdata(), dx))
               y = np.hstack((self._lines[i].get_ydata(), dy))
               # the graph will need to be updated
               update = True
               # As we found the data, no need to search any further
               break

         # In case no matching labels were found, aborting for this curve
         if x is None:
            continue

         # Adjusting the number of points to remain below the length limit
         if self._length and len(x) >= self._length:
            x = x[-self._length:]
            y = y[-self._length:]

         # Dividing the number of points by two to remain below the maxpt limit
         elif len(x) > self._maxpt:
            print(f"[Grapher] Too many points on the graph "
                  f"{i} ({len(x)}>{self._maxpt})")
            x, y = x[::2], y[::2]
            self._factor[i] *= 2
            print(f"[Grapher] Resampling factor is now {self._factor[i]}")

         # Finally, updating the data on the graph
         self._lines[i].set_xdata(x)
         self._lines[i].set_ydata(y)

      # Updating the graph if necessary
      if update:
         self._ax.relim()
         self._ax.autoscale()
         try:
            self._canvas.draw()
         except:
            pass
         self._canvas.flush_events()

   def finish(self) -> None:
      plt.close("all")
      # crappy.stop()
      # arret_de_crappy()

   def _clear(self, *_, **__) -> None:
      for line in self._lines:
         line.set_xdata([])
         line.set_ydata([])
      self.factor = [1 for _ in self._labels]
      self.counter = [0 for _ in self._labels]

class CustomRecorder(crappy.blocks.Recorder):
   """FR : Version qui rajoute nos paramètres au début du fichier.

   EN : Version that adds our parameters at top of the file."""
   def __init__(self, filename, delay = 2, labels = 't(s)', parametres_a_inscrire = {}):
      """Sets the args and initializes the parent class.
      Args:
         filename (:obj:`str`): Path and name of the output file. If the folders
         do not exist, they will be created. If the file already exists, the
         actual file will be named with a trailing number to avoid overriding
         it.
         delay (:obj:`float`, optional): Delay between each write in seconds.
         labels (:obj:`list`, optional): What labels to save. Can be either a
         :obj:`str` to save all labels but this one first, or a :obj:`list` to
         save only these labels.
      """
      crappy.blocks.Recorder.__init__(self, 
                                      filename = filename, 
                                      delay = delay, 
                                      labels = labels)
      self.parametres_a_inscrire = parametres_a_inscrire 

   def begin(self):
      self.last_save = self.t0
      r = self.inputs[0].recv_delay(self.delay)  # To know the actual labels
      if self.labels:
         if not isinstance(self.labels, list):
            if self.labels in r.keys():
               # If one label is specified, place it first and
               # add the others alphabetically
               self.labels = [self.labels]
               for k in sorted(r.keys()):
                  if k not in self.labels:
                     self.labels.append(k)
            else:
               # If not a list but not in labels, forget it and take all the labels
               self.labels = list(sorted(r.keys()))
            # if it is a list, keep it untouched
      else:
         # If we did not give them (False, [] or None):
         self.labels = list(sorted(r.keys()))
      with open(self.filename, 'w') as f:
         for parametre in self.parametres_a_inscrire :      # modified
            f.write(parametre + "\n")                       # modified
         f.write(", ".join(self.labels) + "\n")
      self.save(r)
#V