cmake_minimum_required(VERSION 3.25)
project(py_omni_python_django)

include(../PythonPackage.cmake)

add_python_package(py_omni_python_django
        PACKAGE_NAME omni_python_django
        VERSION 0.1.0
        SOURCES
        src/omni_python_django/__init__.py
        src/omni_python_django/base.py
        src/omni_python_django/_mogrify.py
        )
