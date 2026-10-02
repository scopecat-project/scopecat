"""Keep automated plots off the user's desktop, including in test subprocesses."""

import os

# Set this before collection imports pyplot. Inheriting the environment also
# keeps notebook, analysis and worker subprocesses on the non-interactive backend.
os.environ["MPLBACKEND"] = "Agg"
