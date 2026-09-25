# Vendored code

`tep_python_backend.py` is copied **unmodified** from
`src/tep/python_backend.py` of https://github.com/jkitchin/tennessee-eastman-profbraatz
(commit 9a6c8e5fcef4a2850778704e7793c87b0a187005), licensed BSD-3-Clause (see
`LICENSE.jkitchin`). It is a pure-Python port of TEINIT/TEFUNC from `teprob.f`
and is used only by the optional development backend
(`simulator/tep/python_backend.py`). The authoritative backend is the Fortran
code in `simulator/tep/fortran/src`.

It is vendored (rather than installed with pip) because the upstream package's
build system (`meson-python`, `project('tep', 'c')`) requires a C compiler even
for the Python-only install.
