"""Transform: raw source responses to the output table.

Every source is reshaped into one long format (see
:mod:`myetl.transform.observations`) and then run through generic,
configurable transformers (:mod:`myetl.transform.steps`). A source's
transform is a list of configured transformers, not code of its own.

* :mod:`myetl.transform.observations`: the shared format and periods.
* :mod:`myetl.transform.readers`: response bytes to tables.
* :mod:`myetl.transform.steps`: the generic transformers.
"""
