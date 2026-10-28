"""Bounded pure-Python generator-frame sampling probe; not an app test."""
import faulthandler
import os
import time
import threading
from pathlib import Path

mode = os.environ['PROBE_SAMPLE']
path = Path(os.environ['PROBE_TRACE'])
stream = path.open('w')
stop_sampling = threading.Event()
sampler = None
if mode == 'old':
    faulthandler.dump_traceback_later(0.001, repeat=True, file=stream)
elif mode == 'new':
    def sample():
        while not stop_sampling.wait(0.001):
            stream.write('Python timer sample\n')
            stream.flush()
            faulthandler.dump_traceback(file=stream, all_threads=True)
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()

def select(aliases, received):
    _, alias = next((pair for pair in aliases if pair[1] in received), aliases[0])
    return alias

aliases = [(None, 'a'), (None, 'b'), (None, 'c')]
received = {'c': 1}
stop = time.monotonic() + 3
count = 0
while time.monotonic() < stop:
    select(aliases, received)
    count += 1
if mode == 'old':
    faulthandler.cancel_dump_traceback_later()
if sampler is not None:
    stop_sampling.set()
    sampler.join()
stream.close()
print('completed', count, flush=True)
