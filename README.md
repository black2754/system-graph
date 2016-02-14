What is it?
===========

system-graph.py is a simple script to generate text-mode graphs of various
system resources. It uses unicode block characters to render the graphs.
It was written for use with [tmux](https://tmux.github.io/) but works just as
well in other contexts.

Usage Examples
==============

For the full documentation run `system-graph.py --help`.
The most important command line option is `--format`. It allows to fully
customise how the graph is rendered.

By default, a graph with current stats and nice labels is shown:

![Graph with default settings](default_graph.png)

system-graph.py can also display a short history of recent stats.
For example, the format string `CPU:{cpu[:10]}` displays the 10 most recent CPU
load measurements, again with a nice label.

![Graph with history](graph_history.png)

To conserve space, you can of course remove the labels from the format string
and use ANSII escapes or other means of colourisation to make the stats easily
discernible. The following screenshot shows how such a graph can look when
embedded into the status line of tmux using a horribly long format string.

![Coloured graph in tmux](tmux_coloured.png)

Requirements
============

system-graph.py has a few dependencies:

* Python 3.3 or above
* Linux Kernel 2.6 or above

Since most of the data is obtained from Linux-specific files in `/proc`, it is
unlikely that system-graph.py produces usable results on other operating
systems.

Other than that, no installation is required.
Simply run the script.

License
=======

system-graph.py is licensed under the terms of the Expat/MIT license.
See the file `system-graph.py` for details.
