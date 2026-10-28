"""Bounded pure-Python generator-frame sampling probe; not an app test."""
import faulthandler
import os
import time
from pathlib import Path

sample = os.environ['PROBE_SAMPLE'] == '1'
path = Path(os.environ['PROBE_TRACE'])
stream = path.open('w')
if sample:
    faulthandler.dump_traceback_later(0.001, repeat=True, file=stream)

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
if sample:
    faulthandler.cancel_dump_traceback_later()
stream.close()
print('completed', count, flush=True)
