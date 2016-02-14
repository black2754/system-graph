#!/usr/bin/env python3
# encoding: utf-8

# Copyright (c) 2016 Sebastian Hamann
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import argparse
import json
import multiprocessing
import os
import sys
import time
import _string
from collections import deque, namedtuple
from string import Formatter


NetSpeedTuple = namedtuple('NetSpeedTuple', ['rx', 'tx'])

# Global variable to hold the network interfaces in a consistent order.
interfaces = []
# Global variable to hold the calculated maximum speed values for the network
# interfaces.
max_speed = {}


class GraphFormatter(Formatter):

    """Formatter for the formatstring command line parameter."""

    def get_field(self, field_name, args, kwargs):
        """Find the object `field_name` references.

        :field_name: the field being looked up
        :args: as passed in to vformat
        :kwargs: as passed in to vformat
        :returns: the referenced object and the used field
        """

        # Parse the file_name. first is the keyword, rest contains the
        # attributes and indices, if any.
        first, rest = _string.formatter_field_name_split(field_name)

        # Get the python object referenced by first.
        obj = self.get_value(first, args, kwargs)

        # Loop through the attributes and indices to get the referenced object.
        for is_attr, i in rest:
            if is_attr:
                # i is the name of an attribute.
                if isinstance(obj, list):
                    obj = [getattr(o, i) for o in obj]
                else:
                    obj = getattr(obj, i)
            elif ':' in str(i):
                # i is an index in slice notation.
                obj = obj[slice(*[int(j) if j else None
                                  for j in i.split(':')])]
            else:
                # i is a simple index.
                obj = obj[i]

        return obj, first

    def convert_field(self, value, conversion):
        """Do conversion on the resulting object.

        :value: the value to be converted
        :conversion: the conversion type
        :returns: the converted value
        """

        # Unless something else is requested, convert supported values to UTF-8
        # glyphs using the graph_char function. Fall back to default conversion
        # otherwise.
        if isinstance(value, BaseStat):
            if not conversion:
                return graph_char(value.percentage())
            else:
                return (super(GraphFormatter, self).
                        convert_field(value.percentage(), conversion))
        elif isinstance(value, float) and not conversion:
            return graph_char(value)
        elif isinstance(value, list):
            try:
                return ''.join([self.convert_field(v, conversion)
                                for v in value])
            except TypeError:
                # This can happen, if value is a list of NetSpeed objects. This
                # can not be solved in a sensible manner. Raise a marginally
                # more informative exception.
                raise ValueError('Invalid formatstring.')
        elif isinstance(value, NetSpeed):
            if conversion == 'k' or conversion is None:
                # network speed in kB/s
                return value.kbs()
            elif conversion == 'm':
                # network speed in MB/s
                return value.mbs()
            elif conversion == 'g':
                # network speed in GB/s
                return value.gbs()
            else:
                raise ValueError("Unknown format code '{}'".format(conversion))
        else:
            return super(GraphFormatter, self).convert_field(value, conversion)


class NetSpeed(object):

    """Handle and format network speed data."""

    def __init__(self, speed):
        """Initialise the object.

        :speed: network speed in bytes per second
        """
        self._speed = speed

    def __add__(self, other):
        """Return a NetSpeed object that is the sum of self and other.

        :other: a NetSpeed object
        :returns: a NetSpeed object
        """
        if isinstance(other, NetSpeed):
            return NetSpeed(self._speed + other._speed)
        elif isinstance(other, int):
            return NetSpeed(self._speed + other)
        else:
            raise NotImplementedError()
    # __radd__ is required to be able to use sum
    __radd__ = __add__

    def __gt__(self, other):
        if isinstance(other, NetSpeed):
            return self._speed > other._speed
        else:
            raise NotImplementedError()

    def bs(self):
        """Return the speed in B/s
        :returns: a float
        """
        return self._speed

    def kbs(self):
        """Return the speed in kB/s
        :returns: a float
        """
        return self._speed / 1024

    def mbs(self):
        """Return the speed in MB/s
        :returns: a float
        """
        return self._speed / 1024 ** 2

    def gbs(self):
        """Return the speed in GB/s
        :returns: a float
        """
        return self._speed / 1024 ** 3


class Stats(object):

    """Class that stores all data handled by this tool."""

    def __init__(self, timestamp=None, mem=None, swap=None, loadavg=None,
                 cpu=None, net=None):
        """Initialises the object with given values or current stats.

        :timestamp: UNIX timestamp as float
        :mem: MemStat object
        :swap: SwapStat object
        :loadavg: LoadAvgStat object
        :cpu: CPUStat object
        :net: NetStat object
        """

        self.timestamp = timestamp if timestamp else time.time()
        self.mem = mem if mem else MemStat()
        self.swap = swap if swap else SwapStat()
        self.loadavg = loadavg if loadavg else LoadAvgStat()
        self.cpu = cpu if cpu else CPUStat()
        self.net = net if net else NetStat()


class BaseStat(object):

    """Basic interface for stats."""

    def percentage(self):
        """Return a value between 0 and 1 representing the percentage
        of used resources. More than 1 may be returned to indicate
        over-usage.
        """
        raise NotImplementedError()


class NullStat(BaseStat):

    """Stat that always returns 0 for percentage() and all attributes."""

    def __init__(self, attr=0.0):
        """Initialise the object.

        :attr: return value for undefined attributes
        """
        self._attr = attr

    def __getattr__(self, name):
        """Return 0.
        :name: name of the attribute
        :returns: 0
        """
        return self._attr

    def percentage(self):
        """Return 0.
        :returns: 0
        """
        return 0.0


class MemStat(BaseStat):

    """Memory usage stats."""

    def __init__(self, total=None, free=None):
        """Initialises the object with given values or current stats.

        :total: total system memory (in kB)
        :free: free system memory (in kB)
        """

        self.total = None
        self.free = None
        if total:
            self.total = total
        if free:
            self.free = free
        if not total or not free:
            # Get any missing values from /proc/meminfo.
            with open('/proc/meminfo', 'r') as f:
                # Overwriting free is OK here. If it was set before,
                # self.free is already set and free does not matter
                # anymore.
                free = None
                available = None
                for line in f:
                    if line.startswith('MemTotal') and not self.total:
                        self.total = int(line.split()[1])
                    elif line.startswith('MemAvailable') and not self.free:
                        available = int(line.split()[1])
                    elif line.startswith('MemFree') and not self.free:
                        free = int(line.split()[1])
            if not self.free:
                # Set the amount of free memory. Prefer MemAvailable but fall
                # back to MemFree for older kernels (pre 3.14).
                self.free = available if available else free

    def percentage(self):
        """Return the amount of used memory as a percentage.

        :returns: a value between 0 and 1 representing the amount of
            used memory.
        """
        return (self.total - self.free) / self.total


class SwapStat(BaseStat):

    """Swap usage stats."""

    def __init__(self, total=None, free=None):
        """Initialises the object with given values or current stats.

        :total: total swap space (in kB)
        :free: free swap space (in kB)
        """

        if not total or not free:
            # Get any missing values from /proc/meminfo.
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if line.startswith('SwapTotal') and not total:
                        total = int(line.split()[1])
                    elif line.startswith('SwapFree') and not free:
                        free = int(line.split()[1])
        # Set the attributes.
        self.total = total
        self.free = free

    def percentage(self):
        """Return the amount of used swap space as a percentage.

        :returns: a value between 0 and 1 representing the amount of
            used swap space.
        """
        return (self.total - self.free) / self.total


class LoadAvgStat(BaseStat):

    """Load average stats."""

    def __init__(self, load1=None, load5=None, load15=None):
        """Initialises the object with given values or current stats.

        :load1: load average over the last minute
        :load5: load average over the last five minutes
        :load15: load average over the last fifteen minutes
        """

        if load1 is None or load5 is None or load15 is None:
            load1, load5, load15 = os.getloadavg()
        self.load1 = load1
        self.load5 = load5
        self.load15 = load15

    def __getattr__(self, name):
        """Provide access to normalised load values.
        :name: 1 5 or 15
        :returns: the respective load average divided by the number of
            CPU cores
        """
        if name == '1':
            return self.load1 / multiprocessing.cpu_count()
        elif name == '5':
            return self.load5 / multiprocessing.cpu_count()
        elif name == '15':
            return self.load15 / multiprocessing.cpu_count()
        else:
            raise AttributeError(name)

    def percentage(self):
        """Return the load average of the last minute as a percentage.
        :returns: a value between 0 and 1 representing the 1-minute load
        """
        return getattr(self, '1')


class CPUStat(BaseStat):

    """CPU usage stats."""

    def __init__(self, total=None, idle=None):
        """Initialises the object with given values or current stats.

        :total: total time spent (in ticks)
        :idle: time spent idle (in ticks)
        """

        if total is not None and idle is not None:
            # Set the CPU stats to the given values.
            self.total = total
            self.idle = idle
        else:
            # Obtain the current CPU stats from /proc/stat.
            with open('/proc/stat', 'r') as f:
                for line in f:
                    if line.startswith('cpu '):
                        self.total = sum(int(i) for i in line.split()[1:])
                        self.idle = int(line.split()[4])
                        break

    def percentage(self):
        """Return the CPU usage as a percentage.
        :returns: a value between 0 and 1 representing the CPU usage
        """
        return (self.total - self.idle) / self.total

    def __sub__(self, other):
        """Subtract another CPUStat object.

        :other: a  CPUStat object
        :returns: a CPUStat object
        """
        if isinstance(other, CPUStat):
            return CPUStat(total=self.total - other.total,
                           idle=self.idle - other.idle)
        else:
            raise NotImplementedError()


class NetStat(BaseStat):

    """Network stats."""

    def __init__(self, **kwargs):
        """Initialises the object with given values or current stats."""
        global interfaces
        if kwargs:
            self.__dict__ = kwargs
        else:
            # Detect and initialise interfaces.
            with open('/proc/net/dev', 'r') as f:
                # Skip the first two lines, they contain only headers.
                f.readline()
                f.readline()
                # Loop over the remaining lines and obtain the interface names
                # and stats.
                for line in f:
                    ifname = line.split(':')[0].strip()
                    rx_bytes = int(line.split()[1])
                    tx_bytes = int(line.split()[9])
                    if ifname != 'lo':
                        setattr(self, ifname,
                                IfStat(name=ifname, rx_bytes=rx_bytes,
                                       tx_bytes=tx_bytes, time=time.time()))
                        interfaces.append(ifname)

    def __sub__(self, other):
        """Subtract another NetStat object.

        :other: a NetStat object
        :returns: a NetStat object
        """
        if isinstance(other, NetStat):
            attrs = {}
            for interface in self.__dict__:
                attrs[interface] = (getattr(self, interface) -
                                    getattr(other, interface))
            return NetStat(**attrs)
        else:
            raise NotImplementedError()

    def __getattr__(self, name):
        """Implement rx_speed, tx_speed, rx and tx attributes."""
        if name == 'rx_speed':
            # rx_speed is the measured network speed of all interfaces in bytes
            # per second
            return sum([getattr(self, interface).rx_speed
                        for interface in self.__dict__])
        elif name == 'tx_speed':
            # tx_speed is the measured network speed of all interfaces in bytes
            # per second
            return sum([getattr(self, interface).tx_speed
                        for interface in self.__dict__])
        elif name == 'rx':
            # rx is rx_speed normalised to be between 0 and 1 where 1
            # represents the maximum network speed measured on all interfaces
            try:
                return (self.rx_speed.bs() /
                        sum([max_speed[interface].rx
                             for interface in self.__dict__]).bs())
            except ZeroDivisionError:
                return 0.0
        elif name == 'tx':
            # tx is tx_speed normalised to be between 0 and 1 where 1
            # represents the maximum network speed measured on all interfaces
            try:
                return (self.tx_speed.bs() /
                        sum([max_speed[interface].tx
                             for interface in self.__dict__]).bs())
            except ZeroDivisionError:
                return 0.0
        elif name.isdigit():
            # Return the <name>th IfStat object.
            try:
                return getattr(self, interfaces[int(name)])
            except IndexError:
                raise AttributeError(name)
        else:
            raise AttributeError(name)

    def percentage(self):
        """Return the network usage as a percentage.
        :returns: a value between 0 and 1 representing the network usage
        """
        try:
            return ((self.rx_speed + self.tx_speed).bs() /
                    sum([max_speed[interface].rx + max_speed[interface].tx
                         for interface in self.__dict__]).bs())
        except ZeroDivisionError:
            return 0.0


class IfStat(BaseStat):

    """Network stats for a specific interface."""

    def __init__(self, name, rx_bytes, tx_bytes, time):
        """Initialises the object with given values.

        :name: name of the network interface
        :rx_bytes: number of bytes received
        :tx_bytes: number of bytes transmitted
        :time: period of time over which the data was sent (in seconds)
        """
        self.name = name
        self.rx_bytes = rx_bytes
        self.tx_bytes = tx_bytes
        self.time = time

    def __sub__(self, other):
        """Subtract another IfStat object.

        :other: a IfStat object
        :returns: a IfStat object
        """
        if isinstance(other, IfStat) and self.name == other.name:
            return IfStat(name=self.name,
                          rx_bytes=self.rx_bytes - other.rx_bytes,
                          tx_bytes=self.tx_bytes - other.tx_bytes,
                          time=self.time - other.time)
        else:
            raise NotImplementedError()

    def __getattr__(self, name):
        """Implement rx_speed, tx_speed, rx and tx attributes."""
        if name == 'rx_speed':
            # rx_speed is the measured network speed in bytes per second
            try:
                return NetSpeed(self.rx_bytes / self.time)
            except ZeroDivisionError:
                return NetSpeed(0)
        elif name == 'tx_speed':
            # tx_speed is the measured network speed in bytes per second
            try:
                return NetSpeed(self.tx_bytes / self.time)
            except ZeroDivisionError:
                return NetSpeed(0)
        elif name == 'rx':
            # rx is rx_speed normalised to be between 0 and 1 where 1
            # represents the maximum network speed available on this interface
            try:
                return ((self.rx_bytes / self.time) /
                        max_speed[self.name].rx.bs())
            except ZeroDivisionError:
                return 0.0
        elif name == 'tx':
            # tx is tx_speed normalised to be between 0 and 1 where 1
            # represents the maximum network speed available on this interface
            try:
                return ((self.tx_bytes / self.time) /
                        max_speed[self.name].tx.bs())
            except ZeroDivisionError:
                return 0.0
        else:
            raise AttributeError(name)

    def percentage(self):
        """Return the network usage as a percentage.
        :returns: a value between 0 and 1 representing the network usage
        """
        # This is the speed of received and transmitted data, normalised
        try:
            return (((self.rx_bytes + self.tx_bytes) / self.time) /
                    (max_speed[self.name].rx + max_speed[self.name].tx).bs())
        except ZeroDivisionError:
            return 0.0


def to_json(python_object):
    """Serialise custom objects to the JSON format.

    :python_object: Python object to serialise
    :returns: a structure that can be serialised
    """
    # Check if the object needs serialising.
    if (isinstance(python_object, Stats) or
            isinstance(python_object, BaseStat)):
        # Serialise it as a dict: {<class>: {<data>}}
        return {type(python_object).__name__: python_object.__dict__}
    else:
        # Raise a TypeError if we can not serialise the object.
        raise TypeError(repr(python_object) + ' is not JSON serializable')


def from_json(json_object):
    """Convert JSON objects to Python objects.

    :json_object: the JSON object to deserialise
    :returns: a Python object or the unchanged JSON object
    """
    # Iterate over all supported classes.
    for c in [Stats, MemStat, SwapStat, LoadAvgStat, CPUStat, NetStat, IfStat]:
        # Check if the JSON object is of the form {<c>: {<data>}}.
        if c.__name__ in json_object:
            # Create a new instance of <c>, initialised with <data>.
            return c(**json_object[c.__name__])
    return json_object


def graph_char(percentage):
    """Return the glyph representing `percentage` as close as possible.

    :percentage: value to be represented
    :returns: string consisting of the glyph
    """
    if percentage > 1:
        percentage = 1
    # level contains the UTF-8 glyphs that represent percentages.
    level = [' ']
    level.extend([chr(0x2580 + i) for i in range(1, 9)])
    # step is the accuracy of the representation.
    step = 1 / (len(level) - 1)
    # Find the correct glyph.
    for i in range(0, len(level)):
        if percentage < (i + 0.5) * step:
            return level[i]


def print_graphs(stats, formatstring, max_points):
    """Print the data from `stats` to the console according to
    `formatstring`.

    :stats: data points to consider
    :formatstring: format of the output
    """
    global max_speed
    # The stats are extracted into lists that represent the history of the
    # respective attribute.
    # null is used to fill the lists to the length of max_points entries.
    null = NullStat()
    # Memory stats.
    mem = [s.mem for s in stats][:max_points]
    mem.extend([null] * (max_points - min(len(stats), max_points)))
    # Swap space stats.
    swap = [s.swap for s in stats][:max_points]
    swap.extend([null] * (max_points - min(len(stats), max_points)))
    # Load average stats.
    loadavg = [s.loadavg for s in stats][:max_points]
    loadavg.extend([null] * (max_points - min(len(stats), max_points)))
    # CPU stats are counted over the whole uptime. To get meaningful stats
    # about the current CPU usage, we calculate the differences.
    cpu = []
    for i in range(0, len(stats) - 1):
        cpu.append(stats[i].cpu - stats[i + 1].cpu)
    cpu.extend([null] * (max_points - (len(stats) - 1)))
    # Network stats are counted over the whole uptime as well.
    # The simple NullStat does not provide a sufficient interface to pass as
    # IfStat or NetStat. Thus, we buid up a proper NetStat object with proper
    # IfStat attributes for all interfaces.
    null_ifs = {}
    net = []
    for i in range(0, len(stats) - 1):
        net.append(stats[i].net - stats[i + 1].net)
    for interface in stats[0].net.__dict__:
        null_if = IfStat(name=interface, rx_bytes=0, tx_bytes=0, time=0)
        null_ifs[interface] = null_if
        # Determine the maximum rx and tx speed observed on this interface,
        # but do not go below 1 kB/s
        rx_max = max([getattr(n, interface).rx_speed for n in net] +
                     [NetSpeed(1024)])
        tx_max = max([getattr(n, interface).tx_speed for n in net] +
                     [NetSpeed(1024)])
        max_speed[interface] = NetSpeedTuple(rx_max, tx_max)
    null_net = NetStat(**null_ifs)
    del null_ifs
    net.extend([null_net] * (max_points - (len(stats) - 1)))
    # Print the graph using a GraphFormatter.
    f = GraphFormatter()
    print(f.format(formatstring, mem=mem, swap=swap, loadavg=loadavg, cpu=cpu,
                   net=net))


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Displays minimal graphs for system resources',
        epilog="""
Note: The file $XDG_CONFIG_HOME/system-graph/system-graphrc or
~/.system-graphrc is read and parsed for command line arguments, if it exists.
Parameters from the command line override those from the file.  Parameters in
the file must be separated by newlines. For example
  --max-points 10
does not work. Use
  --max-points
  10
or
  --max-points=10
instead.
Data Points:
    system-graph.py can be run at arbitrary, irregular intervals, but short,
    regular intervals give better results. Each time it is run, a data point is
    made of the current system resources.  These data points are used
    subsequently to generate history graphs.
    The maximum value for the network graphs is also obtained from the
    available data points.
Format Strings:
    The --formatstring parameter controls what data is shown and how it is
    formatted. The format allows for a high amount of customisation, but
    unfortunately requires a lengthy explanation.
    The format string should contain "replacement fields" surrounded by {}.
    Anything that is not contained in braces is considered literal text, which
    is copied unchanged to the output. If you need to include a brace character
    in the literal text, it can be escaped by doubling: {{ and }}.
    The replacement field needs to start with a keyword that specifies the data
    whose value is to be formatted and inserted into the output instead of the
    replacement field. The keyword can be followed by any number of index
    (within []) or attribute (preceded by '.') expressions.
    The keyword is optionally followed by a conversion field, which is preceded
    by '!', and a format specification, which is preceded by ':'. These specify
    a non-default format for the replacement value.
    The following keywords are supported:
        cpu      current CPU usage of all cores
        mem      current memory usage
        swap     current swap usage
        loadavg  system load average
                 The maximum is the number of CPU cores.
        net      current network load
                 The maximum is the maximal network speed observed
    Index expressions:
        Without an index, the full graph for the respective keyword is shown.
        It is always exactly as wide as the value given by --max-points.
        The current reading is on the left, older data points are on the right.
        An index is given in []. Only the readings specified by the index are
        included in the output.
        The index may be a single integer. In this case, the graph consists
        only of the respective data point and is one character wide. 0
        indicates the most recent reading. Example:
            {mem[0]}   show the current memory usage
        Alternatively, the index may be a slice. A slice consists of a lower
        bound, followed by ':', followed by an upper bound, optionally followed
        by ':' and a step. A slice selects all readings including the lower
        bound up to (but not including) the upper bound. If step is given, only
        each <step>th reading from the range is used.
        The lower bound and the upper bound can be omitted. In this case, the
        minimal or maximal possible value is substituted. Examples:
            {mem[0:5]}     Show the five most recent memory usage readings.
            {mem[:]}       Equivalent to {mem}.
            {mem[:5:-1]}   Show the five most recent memory usage readings in
                           reverse order.
    Attributes:
        Some, but not all, keywords have attributes that can be used to get
        more or more specific information. The following attributes are
        supported:
            loadavg:
                1, 5, 15   Show the 1-minute, 5-minutes or 15-minutes load
                           average, respectively. If no attribute is given, the
                           1-minute load average is shown.
                Example:
                {loadavg.15[0]} shows the current load average over the last 15
                minutes.
            net:
                <interface>   Show the network load for the interface named
                              <interface>.
                Example:
                {net.eth0}
                0, 1, ...     Show the network load for the first, seconds, ...
                              interface. The order of the interfaces is
                              obtained from /proc/net/dev.
                Example:
                {net.0}
                rx, tx        Show the downlink or uplink load, respectively.
                              These attributes are also available for
                              specific interfaces.
                Example:
                {net.rx}
                {net.eth0.tx}
                rx_speed, tx_speed  Show the downlink or uplink speed in kB/s.
                                    Also see "Conversion fields" and "Format
                                    specifications" below. Just as rx and tx,
                                    these attributes are available on specific
                                    interfaces.
                                    Note that these attributes is not valid for
                                    a range of readings. A single data point
                                    needs to be specified explicitly!
                Example:
                {net[0].rx_speed}
                {net[0].eth0.tx_speed}
    Conversion fields:
        The optional conversion field is preceded by a '!' and is exactly one
        character long. At this time, only the rx_speed and tx_speed attributes
        have sensible conversion options:
            k   Show the network speed in kB/s (the default).
            m   Show the network speed in MB/s.
            g   Show the network speed in GB/s.
    Format specifications:
        This is only a brief overview of the most important options for format
        specification. The full documentation can be found in the documentation
        of the Python standard library. As stated above, the format
        specification is optional and must be preceded by a ':'.
        An integer may be given after the ':' to specify the minimal width of
        the field.
        It may be followed by a '.' and an integer that defines the precision,
        i.e. the number of digits displayed after the decimal point.
        It may be followed by a character that determined how the data should
        be presented. For network speed, 'f' is probably the most sensible
        option here.
    Examples:
        {mem}
            show a graph of the memory usage, exactly <max_points> wide
        {mem[0]}
            show only the current memory usage without any history, 1 character
        {mem[0:5]}
            show a graph of the memory usage consisting of the five most recent
            data reading
        {mem[::-1]}
            show a graph of the memory usage, with the most recent readings on
            the right
        {loadavg.5}
            show a graph of the 5-minute load averages, exactly <max_points>
            wide
        {loadavg[0].5}
        {loadavg.5[0]}
            show only the current 5-minute load average without any history
        {net}
            show a graph of the network load of all interfaces
        {net.eth0}
            show a graph of the network load of the interface eth0
        {net.0}
            show a graph of the network load of the first interface
        {net.rx}
            show a graph of the downlink network load of all interfaces
        {net.eth0.rx}
            show a graph of the downlink network load of the interface eth0
        {net[0].rx_speed:.2f}
            show the current downlink network speed of all interfaces in kB/s
            with two decimal digits
        {net[0].eth0.rx_speed!m:.2f} MB/s
            show the current downlink network speed of the interface eth0 in
            MB/s with two decimal digits
        Mem:{mem[:3]}|Swap:{swap[:3]}|CPU:{cpu[:3]}|Net:{net[:3]}
            show labeled graphs, separated by |, of memory, swap, CPU and
            network usage, each with a short history of 3 readings""")

    parser.add_argument('--file',
                        default=os.path.join(
                            os.getenv('TMPDIR', '/tmp'),
                            '.{}.system-graph'.format(os.getuid())),
                        help='Location where temporary data is stored '
                        '(default: %(default)s)')
    parser.add_argument('--max-points', default=25, type=int,
                        help='Maximum number of data points to use (default: '
                        '%(default)s)')
    parser.add_argument('--format', dest='formatstring',
                        default='Mem:{mem[0]}|Swap:{swap[0]}|'
                                'Load:{loadavg[0].1}{loadavg[0].5}'
                                '{loadavg[0].15}|CPU:{cpu[0]}|Net:{net[0]}',
                        help='Format string for the graph to be printed (see '
                        'below)')

    # Try reading arguments from the config file.
    paths = []
    paths.append(os.path.join(os.getenv('XDG_CONFIG_HOME', '~/.config'),
                              'system-graph', 'system-graphrc'))
    paths.append('~/.system-graphrc')
    for config_path in [os.path.expanduser(p) for p in paths]:
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                a = [line.strip() for line in f.readlines()]
            break
    else:
        a = []
    # Combine the arguments from the file and the command line and parse both.
    a.extend(sys.argv[1:])
    args = parser.parse_args(a)
    del a
    del paths
    del parser

    # Read the previous data points.
    try:
        with open(args.file, 'r') as f:
            stats = deque(json.load(f, object_hook=from_json),
                          maxlen=args.max_points + 1)
    except FileNotFoundError:
        # Initialise to an empty deque.
        # Deques are similar to lists but more efficient when adding or
        # removing elements at the beginning and able to keep a maximum length.
        stats = deque(maxlen=args.max_points)

    # Create a new data point with current measurements.
    stats.appendleft(Stats())

    # Print the graphs as specified in args.formatstring.
    print_graphs(stats, args.formatstring, args.max_points)

    # Save all data points.
    with open(args.file, 'w') as f:
        json.dump(list(stats), f, default=to_json)

if __name__ == "__main__":
    main()
