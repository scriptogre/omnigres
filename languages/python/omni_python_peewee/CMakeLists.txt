cmake_minimum_required(VERSION 3.25)
project(py_omni_python_peewee)

include(../PythonPackage.cmake)

add_python_package(py_omni_python_peewee
        PACKAGE_NAME omni_python_peewee
        VERSION 0.1.0
        SOURCES
        src/omni_python_peewee/__init__.py
        )
