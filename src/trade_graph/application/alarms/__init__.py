"""Price alarms (T33): the alarm book, the delivery queue a wake dispatcher consumes, and the price watcher.

Import the parts directly (``alarms.book``, ``alarms.queue``, ``alarms.watcher``). Only the watcher reaches the
indicator engine, so the book and the queue load without TA-Lib or NumPy.
"""
