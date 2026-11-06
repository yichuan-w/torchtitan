FROM hamishi740/swerl-tmax-v3:d4482c8993cd
#
# The task environment is BAKED INTO THE IMAGE above. The lines below are this task's
# setup.sh, inlined as comments so a reader -- and the breeding model, which sees only
# instruction.md, this file and solution/solve.sh -- can tell how the environment was built.
# They are COMMENTS on purpose: these scripts are not idempotent and must never run again as
# RUN steps, and a COPY/RUN here would move the image identity the pre-test hook keys on.
# The executable copy ships beside this file as tasks/<id>/setup.sh.
#
# #!/bin/bash
# set -e
# export DEBIAN_FRONTEND=noninteractive
#     apt-get update && apt-get install -y python3 python3-pip python3-dev build-essential
#     pip3 install pytest
#
#     mkdir -p /home/user/metrics_service
#     cd /home/user/metrics_service
#
#     cat << 'EOF' > aggregator.py
# import sys
# import json
# import math
# import csv
# import fast_stats
#
# def main():
#     if len(sys.argv) != 3:
#         print("Usage: python3 aggregator.py <input.csv> <output.json>")
#         sys.exit(1)
#
#     input_csv = sys.argv[1]
#     output_json = sys.argv[2]
#
#     data = []
#     with open(input_csv, 'r') as f:
#         reader = csv.reader(f)
#         for row in reader:
#             data.append(float(row[0]))
#
#     if not data:
#         sys.exit(1)
#
#     mean, stddev = fast_stats.compute_stats(data)
#
#     # TODO: Add assertion-based intermediate validation here
#
#     result = {
#         "mean": mean,
#         "stddev": stddev
#     }
#
#     with open(output_json, 'w') as f:
#         json.dump(result, f)
#
# if __name__ == "__main__":
#     main()
# EOF
#
#     cat << 'EOF' > setup.py
# from setuptools import setup, Extension
#
# # Bug: missing utils.c in the sources list
# module = Extension('fast_stats', sources=['fast_stats.c'])
#
# setup(name='fast_stats',
#       version='1.0',
#       description='Fast statistics computation',
#       ext_modules=[module])
# EOF
#
#     cat << 'EOF' > fast_stats.c
# #define PY_SSIZE_T_CLEAN
# #include <Python.h>
# #include <math.h>
#
# // Defined in utils.c
# extern double compute_variance(double sum, double sum_sq, int count);
#
# static PyObject* compute_stats(PyObject* self, PyObject* args) {
#     PyObject* list_obj;
#     if (!PyArg_ParseTuple(args, "O", &list_obj)) {
#         return NULL;
#     }
#
#     Py_ssize_t count = PyList_Size(list_obj);
#     if (count == 0) {
#         Py_RETURN_NONE;
#     }
#
#     double sum = 0.0;
#     double sum_sq = 0.0;
#
#     for (Py_ssize_t i = 0; i < count; i++) {
#         PyObject* item = PyList_GetItem(list_obj, i);
#         double val = PyFloat_AsDouble(item);
#         sum += val;
#         sum_sq += val * val;
#     }
#
#     double mean = sum / count;
#     double variance = compute_variance(sum, sum_sq, (int)count);
#
#     double stddev = sqrt(variance);
#
#     return Py_BuildValue("dd", mean, stddev);
# }
#
# static PyMethodDef FastStatsMethods[] = {
#     {"compute_stats", compute_stats, METH_VARARGS, "Compute mean and stddev."},
#     {NULL, NULL, 0, NULL}
# };
#
# static struct PyModuleDef faststatsmodule = {
#     PyModuleDef_HEAD_INIT,
#     "fast_stats",
#     NULL,
#     -1,
#     FastStatsMethods
# };
#
# PyMODINIT_FUNC PyInit_fast_stats(void) {
#     return PyModule_Create(&faststatsmodule);
# }
# EOF
#
#     cat << 'EOF' > utils.c
# #include <math.h>
#
# double compute_variance(double sum, double sum_sq, int count) {
#     double mean = sum / count;
#     // Bug: Catastrophic cancellation possible
#     double variance = (sum_sq / count) - (mean * mean);
#     return variance;
# }
# EOF
#
#     cat << 'EOF' > generate_data.py
# import random
# with open('data.csv', 'w') as f:
#     for _ in range(100):
#         # Base 1e9, variation 1e-4 -> float64 will lose precision on sum of squares
#         val = 1000000000.0 + random.uniform(-0.0001, 0.0001)
#         f.write(f"{val}\n")
# EOF
#     python3 generate_data.py
#     rm generate_data.py
#
#     useradd -m -s /bin/bash user || true
#     chmod -R 777 /home/user
