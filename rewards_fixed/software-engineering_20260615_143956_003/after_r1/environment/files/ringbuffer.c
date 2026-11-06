#include <Python.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    PyObject_HEAD
    PyObject** buffer;
    int capacity;
    int head;
    int tail;
    int count;
} RingBufferObject;

static void RingBuffer_dealloc(RingBufferObject* self) {
    for (int i = 0; i < self->count; i++) {
        int idx = (self->head + i) % self->capacity;
        Py_XDECREF(self->buffer[idx]);
    }
    free(self->buffer);
    Py_TYPE(self)->tp_free((PyObject*)self);
}

static PyObject* RingBuffer_new(PyTypeObject* type, PyObject* args, PyObject* kwds) {
    RingBufferObject* self = (RingBufferObject*)type->tp_alloc(type, 0);
    if (self != NULL) {
        self->buffer = NULL;
        self->capacity = 0;
        self->head = 0;
        self->tail = 0;
        self->count = 0;
    }
    return (PyObject*)self;
}

static int RingBuffer_init(RingBufferObject* self, PyObject* args, PyObject* kwds) {
    int capacity;
    if (!PyArg_ParseTuple(args, "i", &capacity))
        return -1;
    if (capacity <= 0) {
        PyErr_SetString(PyExc_ValueError, "capacity must be positive");
        return -1;
    }
    self->buffer = (PyObject**)calloc(capacity, sizeof(PyObject*));
    if (self->buffer == NULL) {
        PyErr_NoMemory();
        return -1;
    }
    self->capacity = capacity;
    self->head = 0;
    self->tail = 0;
    self->count = 0;
    return 0;
}

static PyObject* RingBuffer_push(RingBufferObject* self, PyObject* args) {
    PyObject* item;
    if (!PyArg_ParseTuple(args, "O", &item))
        return NULL;

    Py_INCREF(item);

    if (self->count == self->capacity) {
        /* Buffer full: evict the oldest item to make room */
        self->buffer[self->tail] = item;
        self->tail = (self->tail + 1) % self->capacity;
        self->head = (self->head + 1) % self->capacity;
    } else {
        self->buffer[self->tail] = item;
        self->tail = (self->tail + 1) % self->capacity;
        self->count++;
    }
    Py_RETURN_NONE;
}

static PyObject* RingBuffer_pop(RingBufferObject* self, PyObject* args) {
    if (self->count == 0) {
        PyErr_SetString(PyExc_IndexError, "pop from empty ring buffer");
        return NULL;
    }
    PyObject* item = self->buffer[self->head];
    self->buffer[self->head] = NULL;
    self->head = (self->head + 1) % self->capacity;
    self->count--;
    return item;
}

static PyObject* RingBuffer_peek(RingBufferObject* self, PyObject* args) {
    if (self->count == 0) {
        PyErr_SetString(PyExc_IndexError, "peek from empty ring buffer");
        return NULL;
    }
    PyObject* item = self->buffer[self->head];
    Py_INCREF(item);
    return item;
}

static PyObject* RingBuffer_is_empty(RingBufferObject* self, PyObject* args) {
    if (self->count == 0) {
        Py_RETURN_TRUE;
    }
    Py_RETURN_FALSE;
}

static PyObject* RingBuffer_is_full(RingBufferObject* self, PyObject* args) {
    if (self->count == self->capacity) {
        Py_RETURN_TRUE;
    }
    Py_RETURN_FALSE;
}

static PyObject* RingBuffer_size(RingBufferObject* self, PyObject* args) {
    return PyLong_FromLong(self->count);
}

static PyObject* RingBuffer_capacity(RingBufferObject* self, PyObject* args) {
    return PyLong_FromLong(self->capacity);
}

static PyObject* RingBuffer_clear(RingBufferObject* self, PyObject* args) {
    for (int i = 0; i < self->count; i++) {
        int idx = (self->head + i) % self->capacity;
        Py_DECREF(self->buffer[idx]);
        self->buffer[idx] = NULL;
    }
    self->head = 0;
    self->tail = 0;
    self->count = 0;
    Py_RETURN_NONE;
}

static PyObject* RingBuffer_resize(RingBufferObject* self, PyObject* args) {
    int new_capacity;
    if (!PyArg_ParseTuple(args, "i", &new_capacity))
        return NULL;
    if (new_capacity <= 0) {
        PyErr_SetString(PyExc_ValueError, "capacity must be positive");
        return NULL;
    }

    PyObject** new_buffer = (PyObject**)calloc(new_capacity, sizeof(PyObject*));
    if (new_buffer == NULL) {
        PyErr_NoMemory();
        return NULL;
    }

    int items_to_copy = self->count;
    if (items_to_copy > new_capacity) {
        items_to_copy = new_capacity;
    }

    int start_idx = self->head;
    if (items_to_copy < self->count) {
        /* Shrinking: keep only the items that fit */
        int skip = self->count - items_to_copy;
        start_idx = (self->head + skip) % self->capacity;
    }

    for (int i = 0; i < items_to_copy; i++) {
        int src_idx = (start_idx + i) % self->capacity;
        new_buffer[i] = self->buffer[src_idx];
        self->buffer[src_idx] = NULL;
    }

    free(self->buffer);
    self->buffer = new_buffer;
    self->capacity = new_capacity;
    self->head = 0;
    self->tail = items_to_copy % new_capacity;
    self->count = items_to_copy;
    Py_RETURN_NONE;
}

static PyObject* RingBuffer_to_list(RingBufferObject* self, PyObject* args) {
    PyObject* list = PyList_New(self->count);
    if (list == NULL)
        return NULL;
    for (int i = 0; i < self->count; i++) {
        int idx = (self->head + i) % self->capacity;
        PyObject* item = self->buffer[idx];
        Py_INCREF(item);
        PyList_SET_ITEM(list, i, item);
    }
    return list;
}

static PyObject* RingBuffer_extend(RingBufferObject* self, PyObject* args) {
    PyObject* iterable;
    if (!PyArg_ParseTuple(args, "O", &iterable))
        return NULL;

    PyObject* iterator = PyObject_GetIter(iterable);
    if (iterator == NULL)
        return NULL;

    PyObject* item;
    while ((item = PyIter_Next(iterator)) != NULL) {
        Py_INCREF(item);
        if (self->count == self->capacity) {
            self->buffer[self->tail] = item;
            self->tail = (self->tail + 1) % self->capacity;
            self->head = (self->head + 1) % self->capacity;
        } else {
            self->buffer[self->tail] = item;
            self->tail = (self->tail + 1) % self->capacity;
            self->count++;
        }
    }

    Py_DECREF(iterator);
    if (PyErr_Occurred()) {
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyMethodDef RingBuffer_methods[] = {
    {"push", (PyCFunction)RingBuffer_push, METH_VARARGS,
     "Push an item onto the buffer. If full, the oldest item is evicted."},
    {"pop", (PyCFunction)RingBuffer_pop, METH_NOARGS,
     "Remove and return the oldest item from the buffer."},
    {"peek", (PyCFunction)RingBuffer_peek, METH_NOARGS,
     "Return the oldest item without removing it."},
    {"is_empty", (PyCFunction)RingBuffer_is_empty, METH_NOARGS,
     "Return True if the buffer is empty."},
    {"is_full", (PyCFunction)RingBuffer_is_full, METH_NOARGS,
     "Return True if the buffer is full."},
    {"size", (PyCFunction)RingBuffer_size, METH_NOARGS,
     "Return the number of items currently in the buffer."},
    {"capacity", (PyCFunction)RingBuffer_capacity, METH_NOARGS,
     "Return the maximum capacity of the buffer."},
    {"clear", (PyCFunction)RingBuffer_clear, METH_NOARGS,
     "Remove all items from the buffer."},
    {"resize", (PyCFunction)RingBuffer_resize, METH_VARARGS,
     "Change the buffer capacity. May drop oldest items if shrinking."},
    {"to_list", (PyCFunction)RingBuffer_to_list, METH_NOARGS,
     "Return a list containing all items in the buffer (oldest first)."},
    {"extend", (PyCFunction)RingBuffer_extend, METH_VARARGS,
     "Push all items from an iterable onto the buffer."},
    {NULL, NULL, 0, NULL}
};

static PyTypeObject RingBufferType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "ringbuffer.RingBuffer",
    .tp_basicsize = sizeof(RingBufferObject),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)RingBuffer_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_methods = RingBuffer_methods,
    .tp_init = (initproc)RingBuffer_init,
    .tp_new = RingBuffer_new,
};

static struct PyModuleDef ringbuffermodule = {
    PyModuleDef_HEAD_INIT,
    "ringbuffer",
    "A fixed-size ring buffer for inter-thread communication.",
    -1,
    NULL
};

PyMODINIT_FUNC PyInit_ringbuffer(void) {
    PyObject* m;
    if (PyType_Ready(&RingBufferType) < 0)
        return NULL;
    m = PyModule_Create(&ringbuffermodule);
    if (m == NULL)
        return NULL;
    Py_INCREF(&RingBufferType);
    PyModule_AddObject(m, "RingBuffer", (PyObject*)&RingBufferType);
    return m;
}