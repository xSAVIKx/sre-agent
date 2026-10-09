# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

import os
import warnings

# ADK marks A2A and some features as experimental, and warns at each use. The warnings repeat on
# every run and hide the log lines that explain the run. LOG_LEVEL=DEBUG shows them again.
if os.environ.get("LOG_LEVEL", "").upper() != "DEBUG":
    warnings.filterwarnings("ignore", message=r"\[EXPERIMENTAL\]", category=UserWarning)
